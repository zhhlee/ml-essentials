import codecs
import os
import shutil
import unittest
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from typing import *

import pytest

from mltk import *
from mltk.utils import *


class ConfigTestCase(unittest.TestCase):

    def test_fields(self):
        int_factory = lambda: 12345

        @dataclass
        class MyDataClass(object):
            val: int

        class MyConfig(Config):
            # field definitions
            a: int
            b: Optional[float]

            c = 123.5
            d = None
            e: int = None
            f = config_field(Union[int, float], default_factory=int_factory,
                             description='f field', choices=[12345, 0],
                             required=False)
            g = config_field(int, nullable=True, choices=[123])
            h = config_field(nullable=True)
            i: int = config_field(float)
            j = config_field(default_factory=int_factory)
            k = config_field(Optional[float], nullable=True)
            l = MyDataClass(val=99)

            class nested(Config):
                value: str = 'hello'

            # private field should not be included
            _not_config_field: bool = True

            # property, staticmethod, classmethod and method should not
            # be included
            @property
            def my_prop(self):
                return 123

            @classmethod
            def class_method(cls):
                pass

            @staticmethod
            def static_method():
                pass

            def method(self):
                pass

            # other nested classes should not be included
            class SomeOtherNestedClass(object):
                the_value: str = 'should not include'

        ti = type_info(MyConfig)
        expected_ti = ObjectTypeInfo(
            MyConfig,
            fields={
                'a': ObjectFieldInfo('a', type_info(int)),
                'b': ObjectFieldInfo('b', type_info(Optional[float]),
                                     default=None),
                'c': ObjectFieldInfo('c', type_info(float), default=123.5),
                'd': ObjectFieldInfo(
                    'd', type_info(Optional[Any]), default=None),
                'e': ObjectFieldInfo('e', type_info(int), default=None),
                'f': ObjectFieldInfo(
                    'f', type_info(Union[int, float]),
                    default_factory=int_factory, description='f field',
                    choices=(12345, 0), required=False,
                ),
                'g': ObjectFieldInfo(
                    'g', type_info(Optional[int]), choices=(123,),
                    default=None,
                ),
                'h': ObjectFieldInfo(
                    'h', type_info(Optional[Any]), default=None),
                'i': ObjectFieldInfo('i', type_info(int)),
                'j': ObjectFieldInfo('j', type_info(int),
                                     default_factory=int_factory),
                'k': ObjectFieldInfo('k', type_info(Optional[float]),
                                     default=None),
                'l': ObjectFieldInfo('l', type_info(MyDataClass),
                                     default=MyDataClass(val=99)),
                'nested': ObjectFieldInfo(
                    'nested',
                    ObjectTypeInfo(MyConfig.nested, fields={
                        'value': ObjectFieldInfo(
                            'value', type_info(str), default='hello')
                    }),
                    default_factory=MyConfig.nested
                ),
            }
        )

        self.assertIs(type_info(MyConfig), ti)  # singleton type info
        self.assertEqual(ti, expected_ti)

    def test_checkers(self):
        class MyConfig(Config):
            _a_min: int = 1
            a: int

            @field_checker('a')
            def _a_post_checker(cls, v):
                if v < 4 * cls._a_min:
                    raise ValueError(f'_a_post: must >= {4 * cls._a_min}')
                return v

            @field_checker('*', 'a', pre=True)
            def _any_pre_checker(cls, v):
                if v < 2 * cls._a_min:
                    raise ValueError(f'_any_pre: must >= {2 * cls._a_min}')
                return v

            @root_checker()
            def _root_post_checker(cls, values):
                if values.a < 3 * cls._a_min:
                    raise ValueError(f'_root_post: must >= {3 * cls._a_min}')

            @root_checker(pre=True)
            def _root_pre_checker(cls, values):
                if values['a'] < cls._a_min:
                    raise ValueError(f'_root_pre: must >= {cls._a_min}')

        # test type info
        ti = type_info(MyConfig)
        expected_ti = ObjectTypeInfo(
            MyConfig,
            fields={'a': ObjectFieldInfo('a', type_info(int))},
            field_checkers=[
                ObjectFieldChecker(['a'], MyConfig._a_post_checker, pre=False),
                ObjectFieldChecker(['*'], MyConfig._any_pre_checker, pre=True),
            ],
            root_checkers=[
                ObjectRootChecker(MyConfig._root_post_checker, pre=False),
                ObjectRootChecker(MyConfig._root_pre_checker, pre=True),
            ]
        )
        self.assertEqual(ti, expected_ti)

        # test actual type checking
        self.assertEqual(ti.check_value({'a': 100}), MyConfig(a=100))
        with pytest.raises(TypeCheckError,
                           match='_root_pre: must >= 1'):
            _ = ti.check_value({'a': 0})
        with pytest.raises(TypeCheckError,
                           match='_any_pre: must >= 2'):
            _ = ti.check_value({'a': 1})
        with pytest.raises(TypeCheckError,
                           match='_root_post: must >= 3'):
            _ = ti.check_value({'a': 2})
        with pytest.raises(TypeCheckError,
                           match='_a_post: must >= 4'):
            _ = ti.check_value({'a': 3})

        # test inherit checkers
        class MyMixin:
            _a_min: int = 2

        class MyChild(MyMixin, MyConfig):
            pass

        self.assertEqual(MyChild._a_min, 2)
        ti = type_info(MyChild)
        expected_ti = ObjectTypeInfo(
            MyChild,
            fields={'a': ObjectFieldInfo('a', type_info(int))},
            field_checkers=[
                ObjectFieldChecker(['a'], MyChild._a_post_checker, pre=False),
                ObjectFieldChecker(['*'], MyChild._any_pre_checker, pre=True),
            ],
            root_checkers=[
                ObjectRootChecker(MyChild._root_post_checker, pre=False),
                ObjectRootChecker(MyChild._root_pre_checker, pre=True),
            ]
        )
        self.assertEqual(ti, expected_ti)
        self.assertEqual(ti.check_value({'a': 100}), MyChild(a=100))
        with pytest.raises(TypeCheckError,
                           match='_root_pre: must >= 2'):
            _ = ti.check_value({'a': 1})
        with pytest.raises(TypeCheckError,
                           match='_any_pre: must >= 4'):
            _ = ti.check_value({'a': 3})
        with pytest.raises(TypeCheckError,
                           match='_root_post: must >= 6'):
            _ = ti.check_value({'a': 5})
        with pytest.raises(TypeCheckError,
                           match='_a_post: must >= 8'):
            _ = ti.check_value({'a': 7})

    def test_instance(self):
        envvar = 'MLTK_TEST_C'
        if envvar in os.environ:
            os.environ.pop(envvar)

        @config_params(undefined_fields=True)
        class MyConfig(Config):
            a: int
            b: float = 123.5
            c: str = config_field(required=False, envvar=envvar)

        # construct with empty value
        cfg = MyConfig()
        self.assertEqual(cfg, MyConfig(b=123.5))
        self.assertNotEqual(cfg, MyConfig(b=123.0))
        self.assertNotEqual(cfg, Config(b=123.5))
        self.assertNotEqual(cfg, MyConfig(a=456, b=123.5))

        self.assertEqual(repr(cfg), f'{MyConfig.__qualname__}(b=123.5)')
        self.assertEqual(config_to_dict(cfg), {'b': 123.5})
        self.assertEqual(len(cfg), 1)
        self.assertEqual(list(cfg), ['b'])
        self.assertEqual(cfg['b'], 123.5)
        self.assertEqual(cfg, MyConfig(b=123.5))

        # test setitem, getitem, and delitem
        self.assertIn('b', cfg)
        self.assertNotIn('a', cfg)
        self.assertNotIn('c', cfg)

        cfg['a'] = 456
        self.assertEqual(cfg, MyConfig(a=456, b=123.5))
        self.assertIn('a', cfg)
        self.assertEqual(cfg['a'], 456)
        self.assertEqual(repr(cfg), f'{MyConfig.__qualname__}(a=456, b=123.5)')
        self.assertEqual(config_to_dict(cfg), {'a': 456, 'b': 123.5})
        self.assertEqual(len(cfg), 2)
        self.assertEqual(list(cfg), ['b', 'a'])

        del cfg['b']
        self.assertNotEqual(cfg, MyConfig(a=456, b=123.5))
        self.assertNotEqual(cfg, MyConfig(b=123.5))
        self.assertNotIn('b', cfg)
        self.assertEqual(repr(cfg), f'{MyConfig.__qualname__}(a=456)')
        self.assertEqual(config_to_dict(cfg), {'a': 456})

        # test envvar
        os.environ[envvar] = 'hello, world'
        cfg = MyConfig()
        self.assertEqual(cfg, MyConfig(b=123.5, c='hello, world'))

    def test_to_dict(self):
        @dataclass
        class MyDataClass(object):
            value: int = 3

        class MyConfig(Config):
            a: int = 1

            class nested(Config):
                b: float = 2.0
                data_object = MyDataClass()

        cfg = MyConfig()
        self.assertDictEqual(
            config_to_dict(cfg),
            {'a': 1, 'nested': MyConfig.nested()}
        )
        self.assertDictEqual(
            config_to_dict(cfg, flatten=True),
            {'a': 1, 'nested.b': 2.0, 'nested.data_object.value': 3}
        )

        with pytest.raises(TypeError,
                           match='`o` is neither a Config nor a dataclass '
                                 'object: 123'):
            _ = config_to_dict(123)

    def test_config_defaults(self):
        class MyConfig(Config):
            a: int = 123
            b: float

        expected_defaults = MyConfig(a=123)
        self.assertEqual(config_defaults(MyConfig), expected_defaults)
        self.assertEqual(
            config_defaults(MyConfig(a=456, b=789.0)),
            expected_defaults
        )
        with pytest.raises(TypeError,
                           match='`config` is neither an instance of Config, '
                                 'nor a subclass of Config: got 123'):
            _ = config_defaults(123)


class ConfigLoaderTestCase(unittest.TestCase):

    def test_construction(self):
        class MyConfig(Config):
            pass

        loader = ConfigLoader(MyConfig)
        self.assertIs(loader.config_cls, MyConfig)

        with pytest.raises(TypeError,
                           match='`config_or_cls` is neither a Config class, '
                                 'nor a Config instance: <class \'str\'>'):
            _ = ConfigLoader(str)

    def test_load_object(self):
        class MyConfig(Config):
            class nested1(Config):
                a = 123
                b = ConfigField(float, default=None)

            @config_params(undefined_fields=True)
            class nested2(Config):
                c = 789

        # test feed object of invalid type
        loader = ConfigLoader(MyConfig)
        with pytest.raises(TypeError,
                           match='`key_values` must be a dict or a Config '
                                 'object: got \\[1, 2, 3\\]'):
            loader.load_object([1, 2, 3])

        # test load object
        loader.load_object({
            'nested1': Config(a=1230),
            'nested1.b': 456,
            'nested2.c': '7890',
            'nested2': {'d': 'hello'}
        })
        self.assertEqual(
            loader.get(),
            MyConfig(nested1=MyConfig.nested1(a=1230, b=456.0),
                     nested2=MyConfig.nested2(c=7890, d='hello'))
        )

        # test load object error
        with pytest.raises(ValueError,
                           match='at .nested1.a: cannot merge an object '
                                 'attribute into a non-object attribute'):
            loader.load_object({'nested1.a': 123,
                                'nested1': {'a': Config(value=456)}})

    def test_load_object_nested(self):
        class Nested2(Config):
            b = 456

        @config_params(undefined_fields=True)
        class Nested3(Config):
            c = 789

        class Nested4(Config):
            e = 101112

        class MyConfig(Config):
            class nested1(Config):
                a = 123

            nested2 = ConfigField(Nested2)
            nested3 = Nested3()
            nested4 = ConfigField(default=Nested4())

        loader = ConfigLoader(MyConfig)
        loader.load_object({
            'nested1': Config(a=1230),
            'nested2.b': 4560,
            'nested3.c': 7890,
            'nested3': {'d': 'hello'},
            'nested4': {'e': 1011120}
        })
        self.assertEqual(
            loader.get(),
            MyConfig(nested1=MyConfig.nested1(a=1230),
                     nested2=Nested2(b=4560.0),
                     nested3=Nested3(c=7890, d='hello'),
                     nested4=Nested4(e=1011120))
        )

        loader2 = ConfigLoader(loader.get())
        self.assertEqual(loader2.get(), loader.get())

    def test_load_file(self):
        with TemporaryDirectory() as temp_dir:
            json_file = os.path.join(temp_dir, 'test.json')
            with codecs.open(json_file, 'wb', 'utf-8') as f:
                f.write('{"a": 1, "nested.b": 2}\n')

            yaml_file = os.path.join(temp_dir, 'test.yaml')
            with codecs.open(yaml_file, 'wb', 'utf-8') as f:
                f.write('a: 1\nnested.b: 2\n')

            expected = Config(a=1, nested=Config(b=2))
            loader = ConfigLoader(Config)

            # test load_json
            loader.load_json(json_file)
            self.assertEqual(loader.get(), expected)

            # test load_yaml
            loader.load_yaml(yaml_file)
            self.assertEqual(loader.get(), expected)

            # test load_file
            loader.load_file(json_file)
            self.assertEqual(loader.get(), expected)
            loader.load_file(yaml_file)
            self.assertEqual(loader.get(), expected)

            yaml_file2 = os.path.join(temp_dir, 'test.YML')
            shutil.copy(yaml_file, yaml_file2)
            loader.load_file(yaml_file2)
            self.assertEqual(loader.get(), expected)

            # test unsupported extension
            txt_file = os.path.join(temp_dir, 'test.txt')
            with codecs.open(txt_file, 'wb', 'utf-8') as f:
                f.write('')

            with pytest.raises(IOError,
                               match='Unsupported config file extension: .txt'):
                _ = loader.load_file(txt_file)

    def test_parse_args(self):
        class MyConfig(Config):
            a = 123
            b: Optional[float] = None

            class nested(Config):
                c = ConfigField(str, default=None, nullable=True,
                                choices=['hello', 'bye'])
                d = ConfigField(description='anything, but required')

            e = None
            f = ConfigField(description='anything', required=False)

        # test help message
        loader = ConfigLoader(MyConfig)
        parser = loader.build_arg_parser()
        self.assertRegex(
            parser.format_help(),
            r"[^@]*"
            r"--a\s+int\s+\(default 123\)\s+"
            r"--b\s+Optional\[float\]\s+\(default None\)\s+"
            r"--e\s+Optional\[Any\]\s+\(default None\)\s+"
            r"--f\s+Any\s+anything \(optional\)\s+"
            r"--nested\.c\s+Optional\[str\]\s+\(default None; choices \['hello', 'bye'\]\)\s+"
            r"--nested\.d\s+Any\s+anything, but required\s+\(required\)\s+"
        )

        # test parse
        loader = ConfigLoader(MyConfig)
        loader.parse_args([
            '--nested.c=hello',
            '--nested.d=[1,2,3]',
            '--e={"key":"value"}'  # wrapped by strict dict
        ])
        self.assertEqual(
            loader.get(),
            MyConfig(a=123, b=None, e={'key': 'value'},
                     nested=MyConfig.nested(c='hello', d=[1, 2, 3]))
        )

        # test parse yaml failure, and fallback to str
        loader = ConfigLoader(MyConfig)
        loader.parse_args([
            '--nested.d=[1,2,3',  # not a valid yaml, fallback to str
            '--e={"key":"value"'  # not a valid yaml, fallback to str
        ])
        self.assertEqual(
            loader.get(),
            MyConfig(a=123, b=None, e='{"key":"value"',
                     nested=MyConfig.nested(c=None, d='[1,2,3'))
        )

        # test parse error
        with pytest.raises(ValueError,
                           match=r"Invalid value for argument `--a`; at \.a: "
                                 r"caused by:\n\* ValueError: invalid literal "
                                 r"for int\(\) with base 10: 'xxx'"):
            loader = ConfigLoader(MyConfig)
            loader.parse_args([
                '--a=xxx',
                '--nested.d=True',
            ])
            _ = loader.get()

        with pytest.raises(ValueError,
                           match=r"at nested\.c: invalid value for field 'c'"
                                 r": not one of \['hello', 'bye'\]"):
            loader = ConfigLoader(MyConfig)
            loader.parse_args([
                '--nested.c=invalid',
                '--nested.d=True',
            ])
            _ = loader.get()

        with pytest.raises(ValueError,
                           match=r"at nested\.d: field 'd' is required, "
                                 r"but its value is not specified"):
            loader = ConfigLoader(MyConfig)
            loader.parse_args([])
            _ = loader.get()

    def test_parse_args_nested(self):
        class Nested2(Config):
            b = 456

        class Nested3(Config):
            c = 789

        class MyConfig(Config):
            class nested1(Config):
                a = 123

            nested2 = ConfigField(Nested2)
            nested3 = Nested3()

        # test help message
        loader = ConfigLoader(MyConfig)
        parser = loader.build_arg_parser()
        self.assertRegex(
            parser.format_help(),
            r"[^@]*"
            r"--nested1\.a\s+int\s+\(default 123\)\s+"
            r"--nested2\.b\s+int\s+\(default 456\)\s+"
            r"--nested3\.c\s+int\s+\(default 789\)\s+"
        )

        # test parse
        loader.parse_args([
            '--nested1.a=1230',
            '--nested2.b=4560',
            '--nested3.c=7890'
        ])
        self.assertEqual(
            loader.get(),
            MyConfig(nested1=MyConfig.nested1(a=1230), nested2=Nested2(b=4560),
                     nested3=Nested3(c=7890))
        )
