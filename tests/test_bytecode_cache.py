import os

import pytest

from jinja2 import Environment
from jinja2.bccache import Bucket
from jinja2.bccache import BytecodeCache
from jinja2.bccache import FileSystemBytecodeCache
from jinja2.bccache import MemcachedBytecodeCache
from jinja2.exceptions import TemplateNotFound
from jinja2.loaders import DictLoader
from jinja2.loaders import FileSystemLoader
from jinja2.loaders import FunctionLoader


@pytest.fixture
def env(package_loader, tmp_path):
    bytecode_cache = FileSystemBytecodeCache(str(tmp_path))
    return Environment(loader=package_loader, bytecode_cache=bytecode_cache)


class TestByteCodeCache:
    def test_simple(self, env):
        tmpl = env.get_template("test.html")
        assert tmpl.render().strip() == "BAR"
        pytest.raises(TemplateNotFound, env.get_template, "missing.html")


class MockMemcached:
    class Error(Exception):
        pass

    key = None
    value = None
    timeout = None

    def get(self, key):
        return self.value

    def set(self, key, value, timeout=None):
        self.key = key
        self.value = value
        self.timeout = timeout

    def get_side_effect(self, key):
        raise self.Error()

    def set_side_effect(self, *args):
        raise self.Error()


class TestMemcachedBytecodeCache:
    def test_dump_load(self):
        memcached = MockMemcached()
        m = MemcachedBytecodeCache(memcached)

        b = Bucket(None, "key", "")
        b.code = "code"
        m.dump_bytecode(b)
        assert memcached.key == "jinja2/bytecode/key"

        b = Bucket(None, "key", "")
        m.load_bytecode(b)
        assert b.code == "code"

    def test_exception(self):
        memcached = MockMemcached()
        memcached.get = memcached.get_side_effect
        memcached.set = memcached.set_side_effect
        m = MemcachedBytecodeCache(memcached)
        b = Bucket(None, "key", "")
        b.code = "code"

        m.dump_bytecode(b)
        m.load_bytecode(b)

        m.ignore_memcache_errors = False

        with pytest.raises(MockMemcached.Error):
            m.dump_bytecode(b)

        with pytest.raises(MockMemcached.Error):
            m.load_bytecode(b)


class TestCacheKeyIsolation:
    def test_loader_key_changes_cache_key(self):
        bcc = BytecodeCache.__new__(BytecodeCache)
        key_none = bcc.get_cache_key("test.html")
        key_a = bcc.get_cache_key("test.html", loader_key="LoaderA")
        key_b = bcc.get_cache_key("test.html", loader_key="LoaderB")
        assert key_none != key_a
        assert key_none != key_b
        assert key_a != key_b

    def test_loader_key_stable(self):
        bcc = BytecodeCache.__new__(BytecodeCache)
        key1 = bcc.get_cache_key("t.html", loader_key="X")
        key2 = bcc.get_cache_key("t.html", loader_key="X")
        assert key1 == key2

    def test_different_loader_types_no_cross_hit(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        bcc = FileSystemBytecodeCache(str(cache_dir))

        source = "Hello {{ name }}"
        loader_dict = DictLoader({"index.html": source})
        loader_func = FunctionLoader(
            lambda name: source if name == "index.html" else None
        )

        env1 = Environment(loader=loader_dict, bytecode_cache=bcc)
        env2 = Environment(loader=loader_func, bytecode_cache=bcc)

        tmpl1 = env1.get_template("index.html")
        tmpl2 = env2.get_template("index.html")

        assert tmpl1.render(name="A") == "Hello A"
        assert tmpl2.render(name="B") == "Hello B"

        cache_files = list(cache_dir.iterdir())
        assert len(cache_files) == 2

    def test_different_searchpath_no_cross_hit(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        bcc = FileSystemBytecodeCache(str(cache_dir))
        source = "Result"

        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "t.html").write_text(source)
        (dir_b / "t.html").write_text(source)

        env_a = Environment(
            loader=FileSystemLoader(str(dir_a)), bytecode_cache=bcc
        )
        env_b = Environment(
            loader=FileSystemLoader(str(dir_b)), bytecode_cache=bcc
        )

        tmpl_a = env_a.get_template("t.html")
        tmpl_b = env_b.get_template("t.html")

        assert "a" in tmpl_a.filename
        assert "b" in tmpl_b.filename

        cache_files = list(cache_dir.iterdir())
        assert len(cache_files) == 2

    def test_filename_corrected_from_cache(self, tmp_path):
        """co_filename in cached code is corrected to match the current
        loader's filename, so template.filename is always accurate."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        bcc = FileSystemBytecodeCache(str(cache_dir))

        dir_v1 = tmp_path / "v1"
        dir_v2 = tmp_path / "v2"
        dir_v1.mkdir()
        dir_v2.mkdir()
        (dir_v1 / "page.html").write_text("Hi")
        (dir_v2 / "page.html").write_text("Hi")

        loader_v1 = FileSystemLoader(str(dir_v1))
        env1 = Environment(loader=loader_v1, bytecode_cache=bcc)
        tmpl1 = env1.get_template("page.html")
        assert os.path.normpath(str(dir_v1)) in os.path.normpath(tmpl1.filename)

        loader_v2 = FileSystemLoader(str(dir_v2))
        env2 = Environment(loader=loader_v2, bytecode_cache=bcc)
        tmpl2 = env2.get_template("page.html")
        assert os.path.normpath(str(dir_v2)) in os.path.normpath(tmpl2.filename)
