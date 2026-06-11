"""Tests for async macro + caller + autoescape + Undefined interactions.

Covers the full interaction matrix: async filter, async test, caller(),
autoescape on/off, Undefined propagation, and sync/async consistency.
"""

import pytest
from markupsafe import Markup

from jinja2 import Environment
from jinja2 import Undefined
from jinja2.async_utils import auto_await


# -- Fixtures ---------------------------------------------------------------


@pytest.fixture(params=[True, False], ids=["autoescape", "no_autoescape"])
def autoescape(request):
    return request.param


def _add_filters_tests(env):
    if env.is_async:

        async def upper_fn(value):
            return str(value).upper()

        async def bracket_fn(value):
            return f"[{value}]"

        async def truthy_fn(value):
            return bool(value) and str(value) != ""

        async def short_fn(value):
            return len(str(value)) < 5

    else:

        def upper_fn(value):
            return str(value).upper()

        def bracket_fn(value):
            return f"[{value}]"

        def truthy_fn(value):
            return bool(value) and str(value) != ""

        def short_fn(value):
            return len(str(value)) < 5

    env.filters["async_upper"] = upper_fn
    env.filters["async_bracket"] = bracket_fn
    env.tests["async_truthy"] = truthy_fn
    env.tests["async_short"] = short_fn
    return env


def _render_sync(source, autoescape, **ctx):
    env = _add_filters_tests(Environment(autoescape=autoescape, enable_async=False))
    return env.from_string(source).render(**ctx)


def _render_async(source, autoescape, **ctx):
    env = _add_filters_tests(Environment(autoescape=autoescape, enable_async=True))
    return env.from_string(source).render(**ctx)


def _assert_sync_async_match(source, autoescape, **ctx):
    sync_out = _render_sync(source, autoescape, **ctx)
    async_out = _render_async(source, autoescape, **ctx)
    assert sync_out == async_out, (
        f"sync/async mismatch:\n  sync:  {sync_out!r}\n  async: {async_out!r}"
    )
    return async_out


# -- auto_await fast path ---------------------------------------------------


class TestAutoAwaitFastPath:
    def test_markup_fast_path(self, run_async_fn):
        async def test():
            m = Markup("<b>safe</b>")
            result = await auto_await(m)
            assert result is m
            assert type(result) is Markup

        run_async_fn(test)

    def test_str_fast_path(self, run_async_fn):
        async def test():
            s = "plain"
            result = await auto_await(s)
            assert result is s
            assert type(result) is str

        run_async_fn(test)

    def test_none_fast_path(self, run_async_fn):
        async def test():
            result = await auto_await(None)
            assert result is None

        run_async_fn(test)

    def test_undefined_not_awaited(self, run_async_fn):
        async def test():
            u = Undefined(name="x")
            result = await auto_await(u)
            assert result is u

        run_async_fn(test)

    def test_coroutine_awaited(self, run_async_fn):
        async def coro():
            return 42

        async def test():
            result = await auto_await(coro())
            assert result == 42

        run_async_fn(test)


# -- Macro with async filter ------------------------------------------------


class TestMacroAsyncFilter:
    def test_basic_async_filter(self, autoescape):
        src = """\
{% macro greet(name) %}{{ name|async_upper }}{% endmacro %}\
{{ greet("hello") }}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "HELLO" in out

    def test_async_filter_with_html(self, autoescape):
        src = """\
{% macro show(val) %}{{ val|async_bracket }}{% endmacro %}\
{{ show("<b>") }}"""
        out = _assert_sync_async_match(src, autoescape)
        if autoescape:
            assert "&lt;b&gt;" in out
        else:
            assert "<b>" in out

    def test_async_filter_on_undefined(self, autoescape):
        src = """\
{% macro show(val) %}{{ val|async_upper }}{% endmacro %}\
{{ show() }}"""
        out = _assert_sync_async_match(src, autoescape)
        assert out.strip() == ""


# -- Macro with async test --------------------------------------------------


class TestMacroAsyncTest:
    def test_basic_async_test(self, autoescape):
        src = """\
{% macro check(val) %}\
{% if val is async_truthy %}yes{% else %}no{% endif %}\
{% endmacro %}\
{{ check("hi") }}|{{ check("") }}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "yes" in out
        assert "no" in out

    def test_async_test_on_undefined(self, autoescape):
        src = """\
{% macro check(val) %}\
{% if val is async_truthy %}yes{% else %}no{% endif %}\
{% endmacro %}\
{{ check() }}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "no" in out


# -- Macro with caller ------------------------------------------------------


class TestMacroCaller:
    def test_basic_caller(self, autoescape):
        src = """\
{% macro wrap(tag) %}<{{ tag }}>{{ caller() }}</{{ tag }}>{% endmacro %}\
{% call wrap("div") %}content{% endcall %}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "<div>" in out
        assert "content" in out

    def test_caller_with_html_content(self, autoescape):
        src = """\
{% macro box() %}[{{ caller() }}]{% endmacro %}\
{% call box() %}{{ "<em>hi</em>" }}{% endcall %}"""
        out = _assert_sync_async_match(src, autoescape)
        if autoescape:
            assert "[&lt;em&gt;hi&lt;/em&gt;]" in out
        else:
            assert "[<em>hi</em>]" in out

    def test_caller_undefined_no_call(self, autoescape):
        src = """\
{% macro maybe(val) %}\
{% if val %}{{ caller() }}{% else %}fallback{% endif %}\
{% endmacro %}\
{% call maybe("") %}body{% endcall %}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "fallback" in out


# -- Combined: async filter + async test + caller ---------------------------


class TestCombined:
    def test_filter_and_test_in_macro(self, autoescape):
        src = """\
{% macro process(val) %}\
{% if val is async_truthy %}{{ val|async_upper }}{% else %}EMPTY{% endif %}\
{% endmacro %}\
{{ process("hello") }}|{{ process("") }}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "HELLO" in out
        assert "EMPTY" in out

    def test_filter_test_caller(self, autoescape):
        src = """\
{% macro card(title) %}\
{% if title is async_truthy %}\
{{ title|async_upper }}: {{ caller() }}\
{% else %}\
NO TITLE: {{ caller() }}\
{% endif %}\
{% endmacro %}\
{% call card("news") %}Breaking!{% endcall %}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "NEWS" in out
        assert "Breaking!" in out

    def test_filter_test_caller_false_branch(self, autoescape):
        src = """\
{% macro card(title) %}\
{% if title is async_truthy %}\
{{ title|async_upper }}: {{ caller() }}\
{% else %}\
NO TITLE: {{ caller() }}\
{% endif %}\
{% endmacro %}\
{% call card("") %}Default body{% endcall %}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "NO TITLE" in out
        assert "Default body" in out

    def test_filter_test_caller_undefined_param(self, autoescape):
        src = """\
{% macro card(title) %}\
{% if title is async_truthy %}\
{{ title|async_upper }}:{{ caller() }}\
{% else %}\
NONE:{{ caller() }}\
{% endif %}\
{% endmacro %}\
{% call card() %}fallback{% endcall %}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "NONE" in out
        assert "fallback" in out


# -- Autoescape Markup consistency ------------------------------------------


class TestAutoescapeConsistency:
    def test_macro_result_is_safe_when_autoescape(self):
        src = """\
{% macro m() %}safe content{% endmacro %}\
{{ m() }}"""
        out = _render_async(src, autoescape=True)
        assert "safe content" in out

    def test_caller_html_escaped(self):
        src = """\
{% macro box() %}{{ caller() }}{% endmacro %}\
{% call box() %}{{ "<script>" }}{% endcall %}"""
        out_ae = _render_async(src, autoescape=True)
        out_no = _render_async(src, autoescape=False)
        assert "&lt;script&gt;" in out_ae
        assert "<script>" in out_no

    def test_async_filter_output_escaped(self):
        src = """\
{% macro m(v) %}{{ v|async_bracket }}{% endmacro %}\
{{ m("<img>") }}"""
        out_ae = _render_async(src, autoescape=True)
        out_no = _render_async(src, autoescape=False)
        assert "&lt;img&gt;" in out_ae
        assert "<img>" in out_no

    def test_safe_filter_not_double_escaped(self):
        src = """\
{% macro m(v) %}{{ v|async_bracket }}{% endmacro %}\
{{ m("<b>ok</b>"|safe) }}"""
        out = _render_async(src, autoescape=True)
        assert "[&lt;b&gt;ok&lt;/b&gt;]" in out or "[<b>ok</b>]" in out

    def test_sync_async_autoescape_identical(self):
        src = """\
{% macro card(title) %}\
{% if title is async_truthy %}\
{{ title|async_upper }}: {{ caller() }}\
{% else %}\
NONE: {{ caller() }}\
{% endif %}\
{% endmacro %}\
{% call card("<h1>") %}body <b>bold</b>{% endcall %}"""
        _assert_sync_async_match(src, autoescape=True)
        _assert_sync_async_match(src, autoescape=False)


# -- Undefined propagation --------------------------------------------------


class TestUndefinedPropagation:
    def test_undefined_param_renders_empty(self, autoescape):
        src = """\
{% macro show(val) %}[{{ val }}]{% endmacro %}\
{{ show() }}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "[]" in out

    def test_undefined_through_async_filter(self, autoescape):
        src = """\
{% macro show(val) %}{{ val|async_upper }}{% endmacro %}\
{{ show() }}"""
        out = _assert_sync_async_match(src, autoescape)
        assert out.strip() == ""

    def test_undefined_in_async_test(self, autoescape):
        src = """\
{% macro show(val) %}\
{% if val is async_truthy %}T{% else %}F{% endif %}\
{% endmacro %}\
{{ show() }}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "F" in out

    def test_undefined_branch_with_caller(self, autoescape):
        src = """\
{% macro show(val) %}\
{% if val is async_truthy %}{{ val|async_upper }}{% else %}{{ caller() }}{% endif %}\
{% endmacro %}\
{% call show() %}fallback content{% endcall %}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "fallback content" in out

    def test_undefined_condexpr(self, autoescape):
        src = """\
{% macro show(val) %}\
{{ val|async_upper if val is async_truthy else "default" }}\
{% endmacro %}\
{{ show() }}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "default" in out


# -- Nested call blocks -----------------------------------------------------


class TestNestedCallBlocks:
    def test_nested_call(self, autoescape):
        src = """\
{% macro outer() %}OUTER[{{ caller() }}]{% endmacro %}\
{% macro inner() %}INNER({{ caller() }}){% endmacro %}\
{% call outer() %}{% call inner() %}LEAF{% endcall %}{% endcall %}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "OUTER" in out
        assert "INNER" in out
        assert "LEAF" in out

    def test_nested_call_with_async_filter(self, autoescape):
        src = """\
{% macro outer(label) %}{{ label|async_upper }}[{{ caller() }}]{% endmacro %}\
{% macro inner(label) %}{{ label|async_bracket }}({{ caller() }}){% endmacro %}\
{% call outer("a") %}{% call inner("b") %}leaf{% endcall %}{% endcall %}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "A" in out
        assert "[b]" in out or "[B]" in out
        assert "leaf" in out

    def test_nested_call_undefined_inner(self, autoescape):
        src = """\
{% macro outer() %}O{{ caller() }}{% endmacro %}\
{% macro inner(val) %}\
{% if val is async_truthy %}{{ val }}{% else %}empty{% endif %}\
{% endmacro %}\
{% call outer() %}{{ inner() }}{% endcall %}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "O" in out
        assert "empty" in out


# -- Multiple async filters chained -----------------------------------------


class TestChainedAsyncFilters:
    def test_chained_filters(self, autoescape):
        src = """\
{% macro m(val) %}{{ val|async_upper|async_bracket }}{% endmacro %}\
{{ m("hi") }}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "[HI]" in out

    def test_chained_filters_with_caller(self, autoescape):
        src = """\
{% macro m() %}{{ caller()|async_upper }}{% endmacro %}\
{% call m() %}world{% endcall %}"""
        out = _assert_sync_async_match(src, autoescape)
        assert "WORLD" in out
