import codecs
import os
import shutil
import sys
import zipfile
from tempfile import TemporaryDirectory
from typing import *

from .config import Config, ConfigLoader, config_to_dict, config_defaults
from .events import EventHost, Event
from .mlstorage import MLStorageClient
from .utils import NOT_SET, make_dir_archive, json_dumps, json_loads

__all__ = ['Experiment']

TConfig = TypeVar('TConfig')
ResultDict = Dict[str, Any]


class Experiment(Generic[TConfig]):
    """
    Class to manage the configuration and results of an experiment.

    Basic Usage
    ===========

    To use this class, you first define your experiment config with
    :class:`mltk.Config`, and then wrap your main experiment routine with an
    :class:`Experiment` context, for example, you may write your `main.py` as::

        import numpy as np
        from mltk import Config, Experiment


        class YourConfig(Config):
            max_epoch: int = 100
            learning_rate: float = 0.001
            ...

        if __name__ == '__main__':
            with Experiment(YourConfig) as exp:
                # `exp.config` is the configuration values
                print('Max epoch: ', exp.config.max_epoch)
                print('Learning rate: ', exp.config.learning_rate)

                # `exp.output_dir` is the output directory of this experiment
                print('Output directory: ', exp.output_dir)

                # save result metrics into `exp.output_dir + "/result.json"`
                exp.update_results({'test_acc': ...})

                # write result arrays into `exp.output_dir + "/data.npz"`
                output_file = exp.abspath('data.npz')
                np.savez(output_file, predict=...)

    Then you may execute your python file via::

        python main.py  # use the default config to run the file
        python main.py --max_epoch=200  # override some of the config

    The output directory (i.e., `exp.output_dir`) will be by default
    `"./results/" + script_name`, where `script_name` is the file name of the
    main module (excluding ".py").  The configuration values will be saved
    into `exp.output_dir + "/config.json"`.  If the config file already exists,
    it will be loaded and merged with the config values specified by the CLI
    arguments.  This behavior can allow resuming from an interrupted experiment.

    If the Python script is executed via `mlrun`, then the output directory
    will be assigned by MLStorage server.  For example::

        mlrun -s http://<server>:<port> -- python main.py --max_epoch=200

    You may also get the server URI and the experiment ID assigned by the
    server via the properties `id` and `client`.

    To resume from an interrupted experiment with `mlrun`::

        mlrun --resume-from=<experiment id> -- python main.py

    Since `mlrun` will pick up the output directory of the previous experiment,
    :class:`Experiment` will correctly restore the configuration values from
    `exp.output_dir + "/config.json"`, thus no need to specify the CLI arguments
    once again when resuming from an experiment.
    """

    def __init__(self,
                 config_or_cls: Union[TConfig, Type[TConfig]],
                 script_name: Optional[str] = None,
                 output_dir: Optional[str] = None,
                 load_config_file: bool = True,
                 save_config_file: bool = True,
                 args: Optional[Iterable[str]] = NOT_SET):
        """
        Construct a new :class:`Experiment`.

        Args:
            config_or_cls: The configuration object, or class.
            script_name: The script name.  By default use the file name
                of ``sys.modules['__main__']`` (excluding ".py").
            output_dir: The output directory.  If not specified, use
                `"./results/" + script_name`, or assigned by MLStorage
                server if the experiment is launched by `mlrun`.
            load_config_file: Whether or not to restore configuration
                values from `output_dir + "/config.json"`?
            save_config_file: Whether or not to save configuration
                values into `output_dir + "/config.json"`?
            args: The CLI arguments.  If not specified, use ``sys.argv[1:]``.
                Specifying :obj:`None` will disable parsing the arguments.
        """
        # validate the arguments
        config_or_cls_okay = True
        config = None

        if isinstance(config_or_cls, type):
            if not issubclass(config_or_cls, Config):
                config_or_cls_okay = False
            else:
                config = config_or_cls()
        else:
            if not isinstance(config_or_cls, Config):
                config_or_cls_okay = False
            else:
                config = config_or_cls

        if not config_or_cls_okay:
            raise TypeError(f'`config_or_cls` is neither a Config class, '
                            f'nor a Config instance: {config_or_cls!r}')

        if script_name is None:
            script_name = os.path.splitext(
                os.path.basename(sys.modules['__main__'].__file__))[0]

        if output_dir is None:
            output_dir = os.environ.get('MLSTORAGE_OUTPUT_DIR', None)
        if output_dir is None:
            output_dir = f'./results/{script_name}'
        output_dir = os.path.abspath(output_dir)

        if args is NOT_SET:
            args = sys.argv[1:]
        if args is not None:
            args = tuple(map(str, args))

        # memorize the arguments
        self._script_name = script_name
        self._output_dir = output_dir
        self._config = config
        self._load_config_file = load_config_file
        self._save_config_file = save_config_file
        self._args = args

        # internal state of experiment
        self._results = {}  # type: ResultDict

        # the event
        self._events = EventHost()
        self._on_enter = self.events['on_enter']
        self._on_exit = self.events['on_exit']

        # initialize the MLStorage client if environment variable is set
        id = os.environ.get('MLSTORAGE_EXPERIMENT_ID', None)
        if os.environ.get('MLSTORAGE_SERVER_URI', None):
            client = MLStorageClient(os.environ['MLSTORAGE_SERVER_URI'])
        else:
            client = None

        self._id = id
        self._client = client

    @property
    def id(self) -> Optional[str]:
        """Get the experiment ID, if the environment variable is set."""
        return self._id

    @property
    def client(self) -> Optional[MLStorageClient]:
        """Get the MLStorage client, if the environment variable is set."""
        return self._client

    @property
    def config(self) -> TConfig:
        """
        Get the config object.

        If you would like to modify this object, you may need to manually call
        :meth:`save_config()`, in order to save the modifications to disk.
        """
        return self._config

    @property
    def script_name(self) -> str:
        """Get the script name of this experiment."""
        return self._script_name

    @property
    def output_dir(self) -> str:
        """Get the output directory of this experiment."""
        return self._output_dir

    @property
    def args(self) -> Optional[Tuple[str]]:
        """Get the CLI arguments of this experiment."""
        return self._args

    @property
    def results(self) -> ResultDict:
        """
        Get the results dict of this experiment.

        If you would like to modify this dict, you may need to manually call
        :meth:`save_results()`, in order to save the modifications to disk.
        """
        return self._results

    @property
    def events(self) -> EventHost:
        """Get the event host."""
        return self._events

    @property
    def on_enter(self) -> Event:
        """
        Get the on enter event.

        Callback function type: `() -> None`

        This event will be triggered when entering an experiment context::

            with Experiment(...) as exp:
                # this event will be triggered after entering the context,
                # and before the following statements

                ...
        """
        return self._on_enter

    @property
    def on_exit(self) -> Event:
        """
        Get the on exit event.

        Callback function type: `() -> None`

        This event will be triggered when exiting an experiment context::

            with Experiment(...) as exp:
                ...

                # this event will be triggered after the above statements,
                # and before exiting the context
        """
        return self._on_exit

    def save_config(self):
        """
        Save the config values into `output_dir + "/config.json"`, and the
        default config values into `output_dir + "/config.defaults.json"`.
        """
        config_json = json_dumps(config_to_dict(self.config, flatten=True))
        default_config_json = json_dumps(
            config_to_dict(config_defaults(self.config), flatten=True))

        with codecs.open(os.path.join(self.output_dir, 'config.json'),
                         'wb', 'utf-8') as f:
            f.write(config_json)
        with codecs.open(os.path.join(self.output_dir, 'config.defaults.json'),
                         'wb', 'utf-8') as f:
            f.write(default_config_json)

    def save_results(self):
        """Save the result dict to `output_dir + "/result.json"`."""
        result_file = os.path.join(self.output_dir, 'result.json')

        # load the original results
        old_result = None
        if os.path.isfile(result_file):
            try:
                with codecs.open(result_file, 'rb', 'utf-8') as f:
                    result_json = f.read()
                if result_json:
                    old_result = json_loads(result_json)
                    assert(isinstance(old_result, dict))

            except Exception:  # pragma: no cover
                raise IOError('Cannot load the existing old result.')

        # merge the new result with the old result
        if old_result is not None:
            old_result.update(self.results)
            results = old_result
        else:
            results = self.results

        # now save the new results
        if results:
            result_json = json_dumps(results)
            with codecs.open(result_file, 'wb', 'utf-8') as f:
                f.write(result_json)

    def update_results(self, results: Optional[ResultDict] = None, **kwargs):
        """
        Update the result dict, and save modifications to disk.

        Args:
            results: The dict of updates.
            **kwargs: The named arguments of updates.
        """
        results = dict(results or ())
        results.update(kwargs)
        self._results.update(results)
        self.save_results()

    def abspath(self, relpath: str) -> str:
        """
        Get the absolute path of a relative path in `output_dir`.

        Args:
            relpath: The relative path.

        Returns:
            The absolute path of `relpath`.
        """
        return os.path.join(self.output_dir, relpath)

    def make_dirs(self, relpath: str, exist_ok: bool = True) -> str:
        """
        Create a directory (and its ancestors if necessary) in `output_dir`.

        Args:
            relpath: The relative path of the directory.
            exist_ok: If :obj:`True`, will not raise error if the directory
                already exists.

        Returns:
            The absolute path of `relpath`.
        """
        path = self.abspath(relpath)
        os.makedirs(path, exist_ok=exist_ok)
        return path

    def make_parent(self, relpath: str, exist_ok: bool = True) -> str:
        """
        Create the parent directory of `relpath` (and its ancestors if
        necessary) in `output_dir`.

        Args:
            relpath: The relative path of the entry, whose parent and
                ancestors are to be created.
            exist_ok: If :obj:`True`, will not raise error if the parent
                directory already exists.

        Returns:
            The absolute path of `relpath`.
        """
        path = self.abspath(relpath)
        parent_dir = os.path.split(path)[0]
        os.makedirs(parent_dir, exist_ok=exist_ok)
        return path

    def open_file(self, relpath: str, mode: str, encoding: Optional[str] = None,
                  make_parent: bool = NOT_SET):
        """
        Open a file at `relpath` in `output_dir`.

        Args:
            relpath: The relative path of the file.
            mode: The open mode.
            encoding: The text encoding.  If not specified, will open the file
                in binary mode; otherwise will open it in text mode.
            make_parent: If :obj:`True`, will create the parent (and all
                ancestors) of `relpath` if necessary.  By default, will
                create the parent if open the file by writable mode.

        Returns:
            The opened file.
        """
        if make_parent is NOT_SET:
            make_parent = any(s in mode for s in 'aw+')

        if make_parent:
            path = self.make_parent(relpath)
        else:
            path = self.abspath(relpath)

        if encoding is None:
            return open(path, mode)
        else:
            return codecs.open(path, mode, encoding)

    def put_file_content(self,
                         relpath: str,
                         content: Union[bytes, str],
                         append: bool = False,
                         encoding: Optional[str] = None):
        """
        Save content into a file.

        Args:
            relpath: The relative path of the file.
            content: The file content.  Must be bytes if `encoding` is not
                specified, while text if `encoding` is specified.
            append: Whether or not to append to the file?
            encoding: The text encoding.
        """
        with self.open_file(relpath, 'ab' if append else 'wb',
                            encoding=encoding) as f:
            f.write(content)

    def get_file_content(self, relpath: str, encoding: Optional[str] = None
                         ) -> Union[bytes, str]:
        """
        Get the content of a file.

        Args:
            relpath: The relative path of a file.
            encoding: The text encoding.  If specified, will decode the
                file content using this encoding.

        Returns:
            The file content.
        """
        with self.open_file(relpath, 'rb', encoding=encoding) as f:
            return f.read()

    def make_archive(self,
                     source_dir: str,
                     archive_file: Optional[str] = None,
                     delete_source: bool = True):
        """
        Pack a directory into a zip archive.

        For repeated experiments, pack some result directories into zip
        archives will reduce the total inode count of the file system.

        Args:
            source_dir: The relative path of the source directory.
            archive_file: The relative path of the zip archive.
                If not specified, will use `source_dir + ".zip"`.
            delete_source: Whether or not to delete `source_dir` after
                the zip archive has been created?

        Returns:
            The absolute path of the archive file.
        """
        def _copy_dir(src: str, dst: str):
            os.makedirs(dst, exist_ok=True)

            for name in os.listdir(src):
                f_src = os.path.join(src, name)
                f_dst = os.path.join(dst, name)

                if os.path.isdir(f_src):
                    _copy_dir(f_src, f_dst)
                else:
                    shutil.copyfile(f_src, f_dst, follow_symlinks=False)

        source_dir = self.abspath(source_dir)
        if not os.path.isdir(source_dir):
            raise IOError(f'Not a directory: {source_dir}')

        if archive_file is None:
            archive_file = source_dir.rstrip('/\\') + '.zip'
        else:
            archive_file = self.abspath(archive_file)

        def prepare_parent():
            parent_dir = os.path.dirname(archive_file)
            os.makedirs(parent_dir, exist_ok=True)

        # if the archive already exists, extract it, merge the contents
        # in `source_dir` with the extracted files, and then make archive.
        if os.path.isfile(archive_file):
            with TemporaryDirectory() as temp_dir:
                # extract the original zip
                with zipfile.ZipFile(archive_file, 'r') as zf:
                    zf.extractall(temp_dir)

                # merge the content
                _copy_dir(source_dir, temp_dir)

                # make the destination archive
                prepare_parent()
                make_dir_archive(temp_dir, archive_file)

        # otherwise pack the zip archive directly
        else:
            prepare_parent()
            make_dir_archive(source_dir, archive_file)

        # now delete the source directory
        if delete_source:
            shutil.rmtree(source_dir)

        return archive_file

    def make_archive_on_exit(self,
                             source_dir: str,
                             archive_file: Optional[str] = None,
                             delete_source: bool = True):
        """
        Pack a directory into a zip archive when exiting the experiment context.

        Args:
            source_dir: The relative path of the source directory.
            archive_file: The relative path of the zip archive.
                If not specified, will use `source_dir + ".zip"`.
            delete_source: Whether or not to delete `source_dir` after
                the zip archive has been created?

        See Also:
            :meth:`make_archive()`
        """
        self.on_exit.do(lambda: self.make_archive(
            source_dir=source_dir,
            archive_file=archive_file,
            delete_source=delete_source
        ))

    def __enter__(self) -> 'Experiment[TConfig]':
        config_loader = ConfigLoader(self.config)

        # build the argument parser
        if self.args is not None:
            arg_parser = config_loader.build_arg_parser()
            arg_parser.add_argument(
                '--output-dir', help='Specify the experiment output directory.',
                default=NOT_SET
            )
            parsed_args = arg_parser.parse_args(self.args)

            output_dir = parsed_args.output_dir
            if output_dir is not NOT_SET:
                # special hack: override `output_dir` if specified
                self._output_dir = os.path.abspath(output_dir)
                parsed_args.output_dir = NOT_SET

            parsed_args = {
                key: value for key, value in vars(parsed_args).items()
                if value is not NOT_SET
            }
        else:
            parsed_args = {}

        # load configuration
        config_files = [
            os.path.join(self.output_dir, 'config.yml'),
            os.path.join(self.output_dir, 'config.json'),
        ]
        for config_file in config_files:
            try:
                if os.path.exists(config_file):
                    config_loader.load_file(config_file)
            except Exception:  # pragma: no cover
                raise IOError(f'Failed to load config file: {config_file!r}')

        config_loader.load_object(parsed_args)

        self._config = config_loader.get()

        # prepare for the output dir
        os.makedirs(self.output_dir, exist_ok=True)

        # save the configuration
        if self._save_config_file:
            self.save_config()

        # trigger the on enter event
        self.on_enter.fire()

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.on_exit.fire()
        finally:
            # ensure all changes to configuration and results are saved
            try:
                self.save_config()
            finally:
                self.save_results()
