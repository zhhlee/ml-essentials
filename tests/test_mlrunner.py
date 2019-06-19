import os
import re
import sys
import unittest
import zipfile
from tempfile import TemporaryDirectory

import pytest

from mltk import Config, ConfigValidationError
from mltk.mlrunner import ProgramHost, StdoutParser, MLRunnerConfig, \
    SourceCopier, MLRunnerConfigLoader


def get_file_content(path):
    with open(path, 'rb') as f:
        return f.read()


class MLRunnerConfigTestCase(unittest.TestCase):

    def test_validate(self):
        # test all empty
        config = MLRunnerConfig()
        config.source.includes = None
        config.source.excludes = None

        with pytest.raises(ConfigValidationError,
                           match='`server` is required.'):
            _ = config.validate()

        config.server = 'http://127.0.0.1:8080'

        with pytest.raises(ConfigValidationError,
                           match='`args` is required.'):
            _ = config.validate()

        config.args = ''
        with pytest.raises(ConfigValidationError,
                           match='`args` cannot be empty'):
            _ = config.validate()

        config.args = []
        with pytest.raises(ConfigValidationError,
                           match='`args` cannot be empty'):
            _ = config.validate()

        config.args = ['sh', '-c', 'echo hello']
        config = config.validate()
        for key in ('name', 'description', 'tags', 'env', 'gpu',
                    'work_dir', 'daemon'):
            self.assertIsNone(config[key])
        self.assertIsNone(config.source.includes)
        self.assertIsNone(config.source.excludes)

        # test .args
        config = MLRunnerConfig(args=['sh', 123],
                                server='http://127.0.0.1:8080')
        self.assertEqual(config.validate().args, ['sh', '123'])
        config = MLRunnerConfig(args='exit 0',
                                server='http://127.0.0.1:8080')
        self.assertEqual(config.validate().args, 'exit 0')

        # test .tags
        config.tags = 'hello'
        self.assertListEqual(config.validate().tags, ['hello'])
        config.tags = ['hello', 123]
        self.assertListEqual(config.validate().tags, ['hello', '123'])

        # test .env
        config.env = {'value': 123}
        self.assertEqual(config.validate().env, Config(value='123'))

        # test .gpu
        config.gpu = 1
        self.assertListEqual(config.validate().gpu, [1])
        config.gpu = [1, 2]
        self.assertListEqual(config.validate().gpu, [1, 2])

        # test .daemon
        config.daemon = 'exit 0'
        with pytest.raises(ConfigValidationError,
                           match='`daemon` must be a sequence: got \'exit 0\''):
            _ = config.validate()

        config.daemon = ['exit 0', ['sh', '-c', 'exit 1']]
        self.assertListEqual(config.validate().daemon, [
            'exit 0', ['sh', '-c', 'exit 1']
        ])

        # test .source.includes & .source.excludes using literals
        includes = r'.*\.py$'
        excludes = re.compile(r'.*/\.svn$')

        config.source.includes = includes
        config.source.excludes = excludes

        c = config.validate()
        self.assertIsInstance(c.source.includes, list)
        self.assertEqual(len(c.source.includes), 1)
        self.assertEqual(c.source.includes[0].pattern, includes)

        self.assertIsInstance(c.source.excludes, list)
        self.assertEqual(len(c.source.excludes), 1)
        self.assertIs(c.source.excludes[0], excludes)

        # test .source.includes & .source.excludes using lists
        includes = [r'.*\.py$', re.compile(r'.*\.exe$')]
        excludes = [r'.*/\.git$', re.compile(r'.*/\.svn$')]

        config.source.includes = includes
        config.source.excludes = excludes

        c = config.validate()
        self.assertIsInstance(c.source.includes, list)
        self.assertEqual(len(c.source.includes), 2)
        self.assertEqual(c.source.includes[0].pattern, includes[0])
        self.assertIs(c.source.includes[1], includes[1])

        self.assertIsInstance(c.source.excludes, list)
        self.assertEqual(len(c.source.excludes), 2)
        self.assertEqual(c.source.excludes[0].pattern, excludes[0])
        self.assertIs(c.source.excludes[1], excludes[1])


class MLRunnerConfigLoaderTestCase(unittest.TestCase):

    maxDiff = None

    def test_loader(self):
        with TemporaryDirectory() as temp_dir:
            # prepare for the test dir
            prepare_dir(temp_dir, {
                'sys1': {
                    '.mlrun.yaml': b'clone_from: sys1\n'
                                   b'args: sys1/.mlrun.yaml args\n'
                },
                'sys2': {
                    '.mlrun.yml': b'args: sys2/.mlrun.yml args\n'
                                  b'name: sys2/.mlrun.yml',
                },
                'work': {
                    '.mlrun.yml': b'name: work/.mlrun.yml\n'
                                  b'server: http://127.0.0.1:8080',
                    '.mlrun.yaml': b'server: http://127.0.0.1:8081\n'
                                   b'tags: [1, 2, 3]',
                    '.mlrun.json': b'{"tags": [4, 5, 6],'
                                   b'"description": "work/.mlrun.json"}',
                    'nested': {
                        '.mlrun.yml': b'description: work/nested/.mlrun.yml\n'
                                      b'resume_from: xyz'
                    }
                },
                'config1.yml': b'resume_from: zyx\n'
                               b'source.root: config1',
                'config2.yml': b'source.root: config2\n'
                               b'integration.log_file: config2.log',
            })

            # test loader
            config = MLRunnerConfig(env={'a': '1'}, clone_from='code')
            loader = MLRunnerConfigLoader(
                config=config,
                config_files=[
                    os.path.join(temp_dir, 'config1.yml'),
                    os.path.join(temp_dir, 'config2.yml')
                ],
                work_dir=os.path.join(temp_dir, 'work/nested'),
                system_paths=[
                    os.path.join(temp_dir, 'sys1'),
                    os.path.join(temp_dir, 'sys2')
                ],
            )
            expected_config_files = [
                os.path.join(temp_dir, 'sys1/.mlrun.yaml'),
                os.path.join(temp_dir, 'sys2/.mlrun.yml'),
                os.path.join(temp_dir, 'work/.mlrun.yml'),
                os.path.join(temp_dir, 'work/.mlrun.yaml'),
                os.path.join(temp_dir, 'work/.mlrun.json'),
                os.path.join(temp_dir, 'work/nested/.mlrun.yml'),
                os.path.join(temp_dir, 'config1.yml'),
                os.path.join(temp_dir, 'config2.yml'),
            ]
            self.assertListEqual(
                loader.list_config_files(), expected_config_files)
            load_order = []
            loader.load_config_files(on_load=load_order.append)
            self.assertListEqual(load_order, expected_config_files)

            config = loader.get()
            self.assertEqual(config.integration.log_file, 'config2.log')
            self.assertEqual(config.source.root, 'config2')
            self.assertEqual(config.resume_from, 'zyx')
            self.assertEqual(config.description, 'work/nested/.mlrun.yml')
            self.assertListEqual(config.tags, ['4', '5', '6'])
            self.assertEqual(config.server, 'http://127.0.0.1:8081')
            self.assertEqual(config.name, 'work/.mlrun.yml')
            self.assertEqual(config.args, 'sys2/.mlrun.yml args')
            self.assertEqual(config.clone_from, 'sys1')
            self.assertEqual(config.env, Config(a='1'))

            # test bare loader
            loader = MLRunnerConfigLoader(system_paths=[])
            self.assertListEqual(loader.list_config_files(), [])
            loader.load_config_files()

            # test just one config file
            cfg_file = os.path.join(temp_dir, 'config.json')
            write_file_content(cfg_file, b'{"args": "exit 0",'
                                         b'"server":"http://127.0.0.1:8080"}')
            loader = MLRunnerConfigLoader(config_files=[cfg_file])
            loader.load_config_files()
            self.assertEqual(loader.get(), MLRunnerConfig(
                server='http://127.0.0.1:8080',
                args='exit 0'
            ))


class StdoutParserTestCase(unittest.TestCase):

    def test_parse(self):
        class MyParser(StdoutParser):
            def parse_line(self, line: bytes):
                logs.append(line)

        logs = []
        parser = MyParser()

        parser.parse(b'')
        parser.parse(b'no line break ')
        parser.parse(b'until ')
        parser.parse(b'')
        parser.parse(b'this word\nanother line\nthen the third ')
        parser.parse(b'line')

        self.assertListEqual(logs, [
            b'no line break until this word',
            b'another line',
        ])
        parser.flush()
        self.assertListEqual(logs, [
            b'no line break until this word',
            b'another line',
            b'then the third line',
        ])

        parser.parse(b'')
        parser.parse(b'the fourth line\n')
        parser.parse(b'the fifth line\n')
        parser.flush()
        self.assertListEqual(logs, [
            b'no line break until this word',
            b'another line',
            b'then the third line',
            b'the fourth line',
            b'the fifth line',
        ])


class ProgramHostTestCase(unittest.TestCase):

    def test_run(self):
        def run_and_get_output(*args, **kwargs):
            with TemporaryDirectory() as temp_dir:
                log_file = os.path.join(temp_dir, 'log.txt')
                kwargs.setdefault('log_to_stdout', False)
                kwargs.setdefault('log_file', log_file)
                host = ProgramHost(*args, **kwargs)
                code = host.run()
                if os.path.isfile(log_file):
                    output = get_file_content(log_file)
                else:
                    output = None
                return code, output

        # test exit code
        host = ProgramHost('exit 123', log_to_stdout=False)
        self.assertEqual(host.run(), 123)

        host = ProgramHost(['sh', '-c', 'exit 123'],
                           log_to_stdout=False)
        self.assertEqual(host.run(), 123)

        # test shell command
        if sys.platform == 'win32':
            cmd = 'echo %PYTHONUNBUFFERED%'
        else:
            cmd = 'echo $PYTHONUNBUFFERED'

        code, output = run_and_get_output(cmd)
        self.assertEqual(code, 0)
        self.assertEqual(output, b'1\n')

        # test environment dict
        code, output = run_and_get_output(
            'env',
            env={
                'MY_ENV_VAR': 'hello',
                b'MY_ENV_VAR_2': b'hi',
            },
        )
        self.assertEqual(code, 0)
        self.assertIn(b'MY_ENV_VAR=hello\n', output)
        self.assertIn(b'MY_ENV_VAR_2=hi\n', output)

        # test work dir
        with TemporaryDirectory() as temp_dir:
            temp_dir = os.path.realpath(temp_dir)
            code, output = run_and_get_output('pwd', work_dir=temp_dir)
            self.assertEqual(code, 0)
            self.assertEqual(output, temp_dir.encode('utf-8') + b'\n')

        # test stdout
        with TemporaryDirectory() as temp_dir:
            log_file = os.path.join(temp_dir, 'log.txt')
            fd = os.open(log_file, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
            stdout_fd = sys.stdout.fileno()
            stdout_fd2 = None

            try:
                sys.stdout.flush()
                stdout_fd2 = os.dup(stdout_fd)
                os.dup2(fd, stdout_fd)

                # run the program
                code, output = run_and_get_output(
                    'echo "hello"', log_to_stdout=True)
                self.assertEqual(code, 0)
                self.assertEqual(output, b'hello\n')
                self.assertEqual(get_file_content(log_file), output)
            finally:
                if stdout_fd2 is not None:
                    os.dup2(stdout_fd2, stdout_fd)
                    os.close(stdout_fd2)

        # test log parser
        class MyParser(StdoutParser):
            def parse(self, content: bytes):
                logs.append(content)

            def flush(self):
                logs.append('flush')

        logs = []
        code, output = run_and_get_output(
            [
                r'python', '-c',
                r'import sys, time; '
                r'sys.stdout.write("hello\n"); '
                r'sys.stdout.flush(); '
                r'time.sleep(0.1); '
                r'sys.stdout.write("world\n")'
            ],
            log_parser=MyParser()
        )
        self.assertEqual(code, 0)
        self.assertEqual(output, b'hello\nworld\n')
        self.assertListEqual(logs, [b'hello\n', b'world\n', 'flush'])

        # test log parser with error
        class MyParser(StdoutParser):
            def parse(self, content: bytes):
                logs.append(content)
                raise RuntimeError('some error occurred')

            def flush(self):
                logs.append('flush')
                raise RuntimeError('some error occurred')

        logs = []
        code, output = run_and_get_output(
            [
                r'python', '-c',
                r'import sys, time; '
                r'sys.stdout.write("hello\n"); '
                r'sys.stdout.flush(); '
                r'time.sleep(0.1); '
                r'sys.stdout.write("world\n")'
            ],
            log_parser=MyParser()
        )
        self.assertEqual(code, 0)
        self.assertEqual(output, b'hello\nworld\n')
        self.assertListEqual(logs, [b'hello\n', b'world\n', 'flush'])

        # test log file
        with TemporaryDirectory() as temp_dir:
            log_file = os.path.join(temp_dir, 'log.txt')

            # test append
            code, output = run_and_get_output('echo hello', log_file=log_file)
            self.assertEqual(code, 0)
            code, output = run_and_get_output('echo hi', log_file=log_file)
            self.assertEqual(code, 0)
            self.assertEqual(get_file_content(log_file), b'hello\nhi\n')

            # test no append
            code, output = run_and_get_output(
                'echo hey', log_file=log_file, append_to_file=False)
            self.assertEqual(code, 0)
            self.assertEqual(get_file_content(log_file), b'hey\n')

            # test fileno
            log_fileno = os.open(
                log_file, os.O_CREAT | os.O_TRUNC | os.O_WRONLY)
            try:
                code, output = run_and_get_output(
                    'echo goodbye', log_file=log_fileno)
                self.assertEqual(code, 0)
            finally:
                os.close(log_fileno)
            self.assertEqual(get_file_content(log_file), b'goodbye\n')


def write_file_content(path, content):
    with open(path, 'wb') as f:
        f.write(content)


def dir_snapshot(path):
    ret = {}
    for name in os.listdir(path):
        f_path = os.path.join(path, name)
        if os.path.isdir(f_path):
            ret[name] = dir_snapshot(f_path)
        else:
            ret[name] = get_file_content(f_path)
    return ret


def prepare_dir(path, snapshot):
    os.makedirs(path, exist_ok=True)

    for name, value in snapshot.items():
        f_path = os.path.join(path, name)
        if isinstance(value, dict):
            prepare_dir(f_path, value)
        else:
            write_file_content(f_path, value)


def zip_snapshot(path):
    ret = {}

    def put_entry(arcname, cnt):
        t = ret
        segments = arcname.strip('/').split('/')
        for n in segments[:-1]:
            if n not in t:
                t[n] = {}
            t = t[n]

        assert(segments[-1] not in t)
        t[segments[-1]] = cnt

    with zipfile.ZipFile(path, 'r') as zip_file:
        for e in zip_file.infolist():
            if e.filename.endswith('/'):
                put_entry(e.filename, {})
            else:
                put_entry(e.filename, zip_file.read(e.filename))

    return ret


class SourceCopierTestCase(unittest.TestCase):

    def test_copier(self):
        includes = MLRunnerConfig.source.includes
        excludes = MLRunnerConfig.source.excludes

        with TemporaryDirectory() as temp_dir:
            # prepare for the source dir
            source_dir = os.path.join(temp_dir, 'src')
            prepare_dir(source_dir, {
                'a.py': b'a.py',
                'b.txt': b'b.txt',
                '.git': {
                    'c.py': b'c.py',
                },
                'dir': {
                    'd.sh': b'd.sh',
                },
                'dir2': {
                    'nested': {
                        'e.sh': b'e.sh'
                    },
                    'f.sh': b'f.sh',
                }
            })

            # test copy source
            dest_dir = os.path.join(temp_dir, 'dst')
            copier = SourceCopier(source_dir, dest_dir, includes, excludes)
            copier.clone_dir()
            dest_content = dir_snapshot(dest_dir)
            dest_expected = {
                'a.py': b'a.py',
                'dir': {
                    'd.sh': b'd.sh',
                },
                'dir2': {
                    'nested': {
                        'e.sh': b'e.sh'
                    },
                    'f.sh': b'f.sh',
                }
            }
            self.assertDictEqual(dest_content, dest_expected)

            # test pack zip
            zip_file = os.path.join(temp_dir, 'source.zip')
            copier.pack_zip(zip_file)
            zip_content = zip_snapshot(zip_file)
            self.assertDictEqual(zip_content, dest_expected)

            # test cleanup
            write_file_content(
                os.path.join(dest_dir, 'dir/more.txt'),
                b'more.txt')  # more file
            os.remove(os.path.join(dest_dir, 'dir2/f.sh'))  # fewer file
            copier.cleanup_dir()
            dest_content = dir_snapshot(dest_dir)
            self.assertDictEqual(dest_content, {
                'dir': {
                    'more.txt': b'more.txt'
                }
            })


class JsonFileWatcherTestCase(unittest.TestCase):

    def test_watcher(self):
        pass