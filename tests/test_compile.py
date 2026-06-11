import json
import os
import re

import pytest

from jinja2 import DictLoader
from jinja2 import Environment
from jinja2 import TemplateSyntaxError
from jinja2 import UndefinedError
from jinja2.bccache import bc_version
from jinja2.bccache import BytecodeCache
from jinja2.environment import Environment
from jinja2.loaders import DictLoader
from jinja2.loaders import ModuleLoader


def test_filters_deterministic(tmp_path):
    src = "".join(f"{{{{ {i}|filter{i} }}}}" for i in range(10))
    env = Environment(loader=DictLoader({"foo": src}))
    env.filters.update(dict.fromkeys((f"filter{i}" for i in range(10)), lambda: None))
    env.compile_templates(tmp_path, zip=None)
    name = os.listdir(tmp_path)[0]
    content = (tmp_path / name).read_text("utf8")
    expect = [f"filters['filter{i}']" for i in range(10)]
    found = re.findall(r"filters\['filter\d']", content)
    assert found == expect


def test_import_as_with_context_deterministic(tmp_path):
    src = "\n".join(f'{{% import "bar" as bar{i} with context %}}' for i in range(10))
    env = Environment(loader=DictLoader({"foo": src}))
    env.compile_templates(tmp_path, zip=None)
    name = os.listdir(tmp_path)[0]
    content = (tmp_path / name).read_text("utf8")
    expect = [f"'bar{i}': " for i in range(10)]
    found = re.findall(r"'bar\d': ", content)[:10]
    assert found == expect


def test_top_level_set_vars_unpacking_deterministic(tmp_path):
    src = "\n".join(f"{{% set a{i}, b{i}, c{i} = tuple_var{i} %}}" for i in range(10))
    env = Environment(loader=DictLoader({"foo": src}))
    env.compile_templates(tmp_path, zip=None)
    name = os.listdir(tmp_path)[0]
    content = (tmp_path / name).read_text("utf8")
    expect = [
        f"context.vars.update({{'a{i}': l_0_a{i}, 'b{i}': l_0_b{i}, 'c{i}': l_0_c{i}}})"
        for i in range(10)
    ]
    found = re.findall(
        r"context\.vars\.update\(\{'a\d': l_0_a\d, 'b\d': l_0_b\d, 'c\d': l_0_c\d\}\)",
        content,
    )[:10]
    assert found == expect
    expect = [
        f"context.exported_vars.update(('a{i}', 'b{i}', 'c{i}'))" for i in range(10)
    ]
    found = re.findall(
        r"context\.exported_vars\.update\(\('a\d', 'b\d', 'c\d'\)\)",
        content,
    )[:10]
    assert found == expect


def test_loop_set_vars_unpacking_deterministic(tmp_path):
    src = "\n".join(f"  {{% set a{i}, b{i}, c{i} = tuple_var{i} %}}" for i in range(10))
    src = f"{{% for i in seq %}}\n{src}\n{{% endfor %}}"
    env = Environment(loader=DictLoader({"foo": src}))
    env.compile_templates(tmp_path, zip=None)
    name = os.listdir(tmp_path)[0]
    content = (tmp_path / name).read_text("utf8")
    expect = [
        f"_loop_vars.update({{'a{i}': l_1_a{i}, 'b{i}': l_1_b{i}, 'c{i}': l_1_c{i}}})"
        for i in range(10)
    ]
    found = re.findall(
        r"_loop_vars\.update\(\{'a\d': l_1_a\d, 'b\d': l_1_b\d, 'c\d': l_1_c\d\}\)",
        content,
    )[:10]
    assert found == expect


def test_block_set_vars_unpacking_deterministic(tmp_path):
    src = "\n".join(f"  {{% set a{i}, b{i}, c{i} = tuple_var{i} %}}" for i in range(10))
    src = f"{{% block test %}}\n{src}\n{{% endblock test %}}"
    env = Environment(loader=DictLoader({"foo": src}))
    env.compile_templates(tmp_path, zip=None)
    name = os.listdir(tmp_path)[0]
    content = (tmp_path / name).read_text("utf8")
    expect = [
        f"_block_vars.update({{'a{i}': l_0_a{i}, 'b{i}': l_0_b{i}, 'c{i}': l_0_c{i}}})"
        for i in range(10)
    ]
    found = re.findall(
        r"_block_vars\.update\(\{'a\d': l_0_a\d, 'b\d': l_0_b\d, 'c\d': l_0_c\d\}\)",
        content,
    )[:10]
    assert found == expect


def test_undefined_import_curly_name():
    env = Environment(
        loader=DictLoader(
            {
                "{bad}": "{% from 'macro' import m %}{{ m() }}",
                "macro": "",
            }
        )
    )

    # Must not raise `NameError: 'bad' is not defined`, as that would indicate
    # that `{bad}` is being interpreted as an f-string. It must be escaped.
    with pytest.raises(UndefinedError):
        env.get_template("{bad}").render()


# -----------------------------------------------------------------------
# Manifest generation tests
# -----------------------------------------------------------------------


TEMPLATES_WITH_DEPS = {
    "index.html": (
        '{% extends "layout.html" %}'
        "{% block body %}Hello {{ name }}{% endblock %}"
    ),
    "layout.html": "<html>{% block body %}{% endblock %}</html>",
    "page.html": (
        '{% include "header.html" %}'
        '{% import "macros.html" as m %}'
        "{{ m.greet() }}"
    ),
    "header.html": "<header>{{ title }}</header>",
    "macros.html": "{% macro greet() %}Hi{% endmacro %}",
}


def _make_env(templates=None):
    if templates is None:
        templates = TEMPLATES_WITH_DEPS
    return Environment(loader=DictLoader(templates))


class TestGenerateManifest:
    def test_basic_structure(self):
        env = _make_env()
        manifest = env.generate_manifest()

        assert manifest["version"] == 1
        assert manifest["bc_version"] == bc_version
        assert isinstance(manifest["templates"], list)
        assert len(manifest["templates"]) == len(TEMPLATES_WITH_DEPS)

    def test_entry_fields(self):
        env = _make_env()
        manifest = env.generate_manifest()

        for entry in manifest["templates"]:
            assert "name" in entry
            assert "module_name" in entry
            assert "source_checksum" in entry
            assert "cache_key" in entry
            assert "dependencies" in entry

            # module_name must match ModuleLoader.get_module_filename
            assert entry["module_name"] == ModuleLoader.get_module_filename(
                entry["name"]
            )

    def test_source_checksum_alignment(self):
        """source_checksum must match BytecodeCache.get_source_checksum."""
        env = _make_env()
        manifest = env.generate_manifest()
        bc = BytecodeCache()

        for entry in manifest["templates"]:
            source = TEMPLATES_WITH_DEPS[entry["name"]]
            assert entry["source_checksum"] == bc.get_source_checksum(source)

    def test_cache_key_alignment(self):
        """cache_key must match BytecodeCache.get_cache_key."""
        env = _make_env()
        manifest = env.generate_manifest()
        bc = BytecodeCache()

        for entry in manifest["templates"]:
            # DictLoader returns None for filename
            assert entry["cache_key"] == bc.get_cache_key(entry["name"], None)

    def test_cache_key_with_filename(self, tmp_path):
        """When the loader provides a filename, cache_key must include it."""
        from jinja2 import FileSystemLoader

        (tmp_path / "a.html").write_text("Hello {{ x }}")
        env = Environment(loader=FileSystemLoader(str(tmp_path)))
        manifest = env.generate_manifest()
        bc = BytecodeCache()

        entry = manifest["templates"][0]
        assert entry["name"] == "a.html"
        expected_key = bc.get_cache_key(
            "a.html", str(tmp_path / "a.html")
        )
        assert entry["cache_key"] == expected_key
        # The key should differ from the no-filename variant
        assert entry["cache_key"] != bc.get_cache_key("a.html", None)

    def test_dependencies_extends(self):
        env = _make_env()
        manifest = env.generate_manifest()
        by_name = {e["name"]: e for e in manifest["templates"]}

        assert "layout.html" in by_name["index.html"]["dependencies"]

    def test_dependencies_include(self):
        env = _make_env()
        manifest = env.generate_manifest()
        by_name = {e["name"]: e for e in manifest["templates"]}

        assert "header.html" in by_name["page.html"]["dependencies"]

    def test_dependencies_import(self):
        env = _make_env()
        manifest = env.generate_manifest()
        by_name = {e["name"]: e for e in manifest["templates"]}

        assert "macros.html" in by_name["page.html"]["dependencies"]

    def test_dependencies_no_deps(self):
        env = _make_env()
        manifest = env.generate_manifest()
        by_name = {e["name"]: e for e in manifest["templates"]}

        assert by_name["layout.html"]["dependencies"] == []
        assert by_name["header.html"]["dependencies"] == []
        assert by_name["macros.html"]["dependencies"] == []

    def test_dynamic_dependency_omitted(self):
        """Dynamic includes (variable template names) produce None in
        find_referenced_templates and must be filtered out of the manifest."""
        env = Environment(
            loader=DictLoader(
                {
                    "dyn.html": '{% include var %}{% extends "base.html" %}',
                    "base.html": "base",
                }
            )
        )
        manifest = env.generate_manifest()
        by_name = {e["name"]: e for e in manifest["templates"]}

        deps = by_name["dyn.html"]["dependencies"]
        assert "base.html" in deps
        assert None not in deps

    def test_ignore_errors_true_skips_bad_templates(self):
        env = Environment(
            loader=DictLoader(
                {
                    "good.html": "Hello {{ name }}",
                    "bad.html": "{% invalid_tag %}",
                }
            )
        )
        manifest = env.generate_manifest(ignore_errors=True)
        names = {e["name"] for e in manifest["templates"]}
        assert "good.html" in names
        assert "bad.html" not in names

    def test_ignore_errors_false_raises(self):
        env = Environment(
            loader=DictLoader(
                {
                    "good.html": "Hello",
                    "bad.html": "{% invalid_tag %}",
                }
            )
        )
        with pytest.raises(TemplateSyntaxError):
            env.generate_manifest(ignore_errors=False)

    def test_extensions_filter(self):
        env = Environment(
            loader=DictLoader(
                {
                    "a.html": "A",
                    "b.txt": "B",
                    "c.html": "C",
                }
            )
        )
        manifest = env.generate_manifest(extensions=["html"])
        names = {e["name"] for e in manifest["templates"]}
        assert names == {"a.html", "c.html"}

    def test_filter_func(self):
        env = Environment(
            loader=DictLoader(
                {
                    "keep_a.html": "A",
                    "skip_b.html": "B",
                    "keep_c.html": "C",
                }
            )
        )
        manifest = env.generate_manifest(
            filter_func=lambda n: n.startswith("keep_")
        )
        names = {e["name"] for e in manifest["templates"]}
        assert names == {"keep_a.html", "keep_c.html"}

    def test_empty_loader(self):
        env = Environment(loader=DictLoader({}))
        manifest = env.generate_manifest()
        assert manifest["version"] == 1
        assert manifest["bc_version"] == bc_version
        assert manifest["templates"] == []

    def test_no_loader_raises(self):
        env = Environment()
        with pytest.raises(AssertionError, match="No loader"):
            env.generate_manifest()

    def test_checksum_changes_on_source_change(self):
        """Changing template source must change the checksum."""
        env1 = Environment(loader=DictLoader({"t.html": "version1"}))
        env2 = Environment(loader=DictLoader({"t.html": "version2"}))
        m1 = env1.generate_manifest()
        m2 = env2.generate_manifest()
        assert (
            m1["templates"][0]["source_checksum"]
            != m2["templates"][0]["source_checksum"]
        )

    def test_cache_key_stable_for_same_name(self):
        """The cache_key depends on the name, not the source."""
        env1 = Environment(loader=DictLoader({"t.html": "version1"}))
        env2 = Environment(loader=DictLoader({"t.html": "version2"}))
        m1 = env1.generate_manifest()
        m2 = env2.generate_manifest()
        assert (
            m1["templates"][0]["cache_key"]
            == m2["templates"][0]["cache_key"]
        )


class TestCompileTemplatesManifest:
    def test_dir_manifest_default_name(self, tmp_path):
        env = _make_env()
        env.compile_templates(str(tmp_path), zip=None, manifest=True)

        manifest_path = tmp_path / "manifest.json"
        assert manifest_path.exists()

        m = json.loads(manifest_path.read_text("utf-8"))
        assert m["version"] == 1
        assert m["bc_version"] == bc_version
        assert len(m["templates"]) == len(TEMPLATES_WITH_DEPS)

    def test_dir_manifest_custom_name(self, tmp_path):
        env = _make_env()
        env.compile_templates(
            str(tmp_path), zip=None, manifest="build_info.json"
        )

        assert (tmp_path / "build_info.json").exists()
        assert not (tmp_path / "manifest.json").exists()

    def test_dir_no_manifest(self, tmp_path):
        env = _make_env()
        env.compile_templates(str(tmp_path), zip=None)

        assert not (tmp_path / "manifest.json").exists()
        # Only .py files
        assert all(f.endswith(".py") for f in os.listdir(tmp_path))

    def test_zip_manifest_default_name(self, tmp_path):
        from zipfile import ZipFile

        zip_path = tmp_path / "out.zip"
        env = _make_env()
        env.compile_templates(str(zip_path), zip="deflated", manifest=True)

        with ZipFile(str(zip_path)) as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            m = json.loads(zf.read("manifest.json"))
            assert m["version"] == 1
            assert len(m["templates"]) == len(TEMPLATES_WITH_DEPS)

    def test_zip_manifest_custom_name(self, tmp_path):
        from zipfile import ZipFile

        zip_path = tmp_path / "out.zip"
        env = _make_env()
        env.compile_templates(
            str(zip_path), zip="deflated", manifest="info.json"
        )

        with ZipFile(str(zip_path)) as zf:
            names = zf.namelist()
            assert "info.json" in names
            assert "manifest.json" not in names

    def test_zip_no_manifest(self, tmp_path):
        from zipfile import ZipFile

        zip_path = tmp_path / "out.zip"
        env = _make_env()
        env.compile_templates(str(zip_path), zip="deflated")

        with ZipFile(str(zip_path)) as zf:
            names = zf.namelist()
            assert "manifest.json" not in names
            assert all(n.endswith(".py") for n in names)

    def test_manifest_checksum_matches_bytecode_cache(self, tmp_path):
        """Manifest checksums from compile_templates must match
        BytecodeCache methods exactly."""
        env = _make_env()
        env.compile_templates(str(tmp_path), zip=None, manifest=True)

        m = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
        bc = BytecodeCache()

        for entry in m["templates"]:
            source = TEMPLATES_WITH_DEPS[entry["name"]]
            assert entry["source_checksum"] == bc.get_source_checksum(source)
            assert entry["cache_key"] == bc.get_cache_key(entry["name"], None)

    def test_manifest_module_name_matches_module_loader(self, tmp_path):
        """module_name in manifest must match ModuleLoader convention."""
        env = _make_env()
        env.compile_templates(str(tmp_path), zip=None, manifest=True)

        m = json.loads((tmp_path / "manifest.json").read_text("utf-8"))

        for entry in m["templates"]:
            expected = ModuleLoader.get_module_filename(entry["name"])
            assert entry["module_name"] == expected
            # The compiled .py file should actually exist
            assert (tmp_path / expected).exists()

    def test_manifest_dependencies_in_compile(self, tmp_path):
        env = _make_env()
        env.compile_templates(str(tmp_path), zip=None, manifest=True)

        m = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
        by_name = {e["name"]: e for e in m["templates"]}

        assert "layout.html" in by_name["index.html"]["dependencies"]
        assert "header.html" in by_name["page.html"]["dependencies"]
        assert "macros.html" in by_name["page.html"]["dependencies"]

    def test_manifest_ignore_errors_skips_bad(self, tmp_path):
        env = Environment(
            loader=DictLoader(
                {
                    "good.html": "Hello {{ name }}",
                    "bad.html": "{% broken %}",
                }
            )
        )
        env.compile_templates(
            str(tmp_path), zip=None, manifest=True, ignore_errors=True
        )

        m = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
        names = {e["name"] for e in m["templates"]}
        assert "good.html" in names
        assert "bad.html" not in names

    def test_manifest_ignore_errors_false_aborts(self, tmp_path):
        env = Environment(
            loader=DictLoader(
                {
                    "good.html": "Hello",
                    "bad.html": "{% broken %}",
                }
            )
        )
        with pytest.raises(TemplateSyntaxError):
            env.compile_templates(
                str(tmp_path),
                zip=None,
                manifest=True,
                ignore_errors=False,
            )

    def test_manifest_stored_zip(self, tmp_path):
        """manifest works with zip='stored' algorithm too."""
        from zipfile import ZipFile

        zip_path = tmp_path / "out.zip"
        env = _make_env()
        env.compile_templates(
            str(zip_path), zip="stored", manifest=True
        )

        with ZipFile(str(zip_path)) as zf:
            assert "manifest.json" in zf.namelist()
            m = json.loads(zf.read("manifest.json"))
            assert len(m["templates"]) == len(TEMPLATES_WITH_DEPS)

    def test_generate_manifest_matches_compile_manifest(self, tmp_path):
        """generate_manifest and compile_templates(manifest=True) must
        produce identical template entries."""
        env = _make_env()

        standalone = env.generate_manifest()

        env.compile_templates(str(tmp_path), zip=None, manifest=True)
        compiled = json.loads(
            (tmp_path / "manifest.json").read_text("utf-8")
        )

        # Sort both by name for stable comparison
        s_entries = sorted(standalone["templates"], key=lambda e: e["name"])
        c_entries = sorted(compiled["templates"], key=lambda e: e["name"])

        assert len(s_entries) == len(c_entries)
        for s, c in zip(s_entries, c_entries):
            assert s["name"] == c["name"]
            assert s["module_name"] == c["module_name"]
            assert s["source_checksum"] == c["source_checksum"]
            assert s["cache_key"] == c["cache_key"]
            assert sorted(s["dependencies"]) == sorted(c["dependencies"])
