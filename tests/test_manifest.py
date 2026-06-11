import json
import os
from zipfile import ZipFile

import pytest

from jinja2 import TemplateSyntaxError
from jinja2.bccache import BytecodeCache
from jinja2.environment import Environment
from jinja2.loaders import DictLoader
from jinja2.loaders import ModuleLoader


@pytest.fixture
def env_with_deps():
    return Environment(
        loader=DictLoader(
            {
                "base.html": "<html>{% block body %}{% endblock %}</html>",
                "child.html": '{% extends "base.html" %}{% block body %}hi{% endblock %}',
                "partial.html": "<p>partial</p>",
                "page.html": '{% include "partial.html" %}content',
            }
        )
    )


def test_generate_manifest_directory(env_with_deps, tmp_path):
    result = env_with_deps.generate_manifest(tmp_path, zip=None)

    manifest_path = tmp_path / "manifest.json"
    assert manifest_path.exists()

    with open(manifest_path, encoding="utf-8") as f:
        on_disk = json.load(f)

    assert result == on_disk
    assert result["version"] == 1
    assert isinstance(result["templates"], list)
    assert len(result["templates"]) == 4
    assert result["errors"] == []

    names = {e["name"] for e in result["templates"]}
    assert names == {"base.html", "child.html", "partial.html", "page.html"}

    for entry in result["templates"]:
        assert "name" in entry
        assert "module" in entry
        assert "cache_key" in entry
        assert "source_checksum" in entry
        assert "dependencies" in entry
        assert entry["module"] == ModuleLoader.get_module_filename(entry["name"])


def test_generate_manifest_zip(env_with_deps, tmp_path):
    zip_path = tmp_path / "manifest.zip"
    result = env_with_deps.generate_manifest(zip_path, zip="deflated")

    assert zip_path.exists()
    with ZipFile(zip_path, "r") as zf:
        assert "manifest.json" in zf.namelist()
        on_disk = json.loads(zf.read("manifest.json").decode("utf-8"))

    assert result == on_disk
    assert len(result["templates"]) == 4


def test_generate_manifest_zip_stored(env_with_deps, tmp_path):
    zip_path = tmp_path / "manifest.zip"
    result = env_with_deps.generate_manifest(zip_path, zip="stored")

    with ZipFile(zip_path, "r") as zf:
        on_disk = json.loads(zf.read("manifest.json").decode("utf-8"))

    assert result == on_disk


def test_manifest_cache_key_alignment(tmp_path):
    templates = {"hello.html": "Hello {{ name }}"}
    env = Environment(loader=DictLoader(templates))
    result = env.generate_manifest(tmp_path, zip=None)

    entry = result["templates"][0]
    source, filename, _ = env.loader.get_source(env, "hello.html")
    bcc = BytecodeCache()
    expected_key = bcc.get_cache_key("hello.html", filename)

    assert entry["cache_key"] == expected_key


def test_manifest_source_checksum_alignment(tmp_path):
    templates = {"hello.html": "Hello {{ name }}"}
    env = Environment(loader=DictLoader(templates))
    result = env.generate_manifest(tmp_path, zip=None)

    entry = result["templates"][0]
    source, _, _ = env.loader.get_source(env, "hello.html")
    bcc = BytecodeCache()
    expected_checksum = bcc.get_source_checksum(source)

    assert entry["source_checksum"] == expected_checksum


def test_manifest_syntax_error_ignore(tmp_path):
    templates = {
        "good.html": "Hello",
        "bad.html": "{% if %}broken",
    }
    env = Environment(loader=DictLoader(templates))
    log_messages = []
    result = env.generate_manifest(
        tmp_path, zip=None, log_function=log_messages.append, ignore_errors=True
    )

    assert len(result["templates"]) == 1
    assert result["templates"][0]["name"] == "good.html"
    assert len(result["errors"]) == 1
    assert result["errors"][0]["name"] == "bad.html"
    assert "error" in result["errors"][0]
    assert any("bad.html" in msg for msg in log_messages)


def test_manifest_syntax_error_raise(tmp_path):
    templates = {
        "good.html": "Hello",
        "bad.html": "{% if %}broken",
    }
    env = Environment(loader=DictLoader(templates))

    with pytest.raises(TemplateSyntaxError):
        env.generate_manifest(tmp_path, zip=None, ignore_errors=False)


def test_manifest_dependencies(env_with_deps, tmp_path):
    result = env_with_deps.generate_manifest(tmp_path, zip=None)

    by_name = {e["name"]: e for e in result["templates"]}

    assert by_name["child.html"]["dependencies"] == ["base.html"]
    assert by_name["page.html"]["dependencies"] == ["partial.html"]
    assert by_name["base.html"]["dependencies"] == []
    assert by_name["partial.html"]["dependencies"] == []


def test_manifest_dynamic_dependency(tmp_path):
    templates = {
        "main.html": "{% include dynamic_var %}content",
    }
    env = Environment(loader=DictLoader(templates))
    result = env.generate_manifest(tmp_path, zip=None)

    entry = result["templates"][0]
    assert None in entry["dependencies"]


def test_manifest_mixed_dependencies(tmp_path):
    templates = {
        "main.html": '{% include "static.html" %}{% include dynamic_var %}',
        "static.html": "static",
    }
    env = Environment(loader=DictLoader(templates))
    result = env.generate_manifest(tmp_path, zip=None)

    by_name = {e["name"]: e for e in result["templates"]}
    deps = by_name["main.html"]["dependencies"]
    assert "static.html" in deps
    assert None in deps
    assert deps.index("static.html") < deps.index(None)


def test_manifest_filter_func(env_with_deps, tmp_path):
    result = env_with_deps.generate_manifest(
        tmp_path,
        zip=None,
        filter_func=lambda name: name.startswith("base"),
    )

    assert len(result["templates"]) == 1
    assert result["templates"][0]["name"] == "base.html"


def test_manifest_extensions_filter(tmp_path):
    templates = {
        "page.html": "html",
        "page.txt": "text",
        "style.css": "css",
    }
    env = Environment(loader=DictLoader(templates))
    result = env.generate_manifest(tmp_path, zip=None, extensions=["html"])

    names = {e["name"] for e in result["templates"]}
    assert names == {"page.html"}


def test_manifest_return_value(env_with_deps, tmp_path):
    result = env_with_deps.generate_manifest(tmp_path, zip=None)

    assert isinstance(result, dict)
    assert "version" in result
    assert "templates" in result
    assert "errors" in result


def test_manifest_creates_target_directory(tmp_path):
    target = tmp_path / "sub" / "dir"
    env = Environment(loader=DictLoader({"a.html": "hello"}))
    env.generate_manifest(target, zip=None)

    assert (target / "manifest.json").exists()


def test_manifest_log_function(env_with_deps, tmp_path):
    log_messages = []
    env_with_deps.generate_manifest(
        tmp_path, zip=None, log_function=log_messages.append
    )

    assert any("Processed" in msg for msg in log_messages)
    assert any("Wrote manifest" in msg for msg in log_messages)
