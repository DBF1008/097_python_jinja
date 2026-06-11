"""Tests for structured dependency analysis (jinja2.dependency)."""

import os

import pytest

from jinja2 import DictLoader
from jinja2 import Environment
from jinja2.dependency import Dependency
from jinja2.dependency import DependencyGraph
from jinja2.dependency import DependencyType
from jinja2.dependency import build_dependency_graph
from jinja2.dependency import build_dependency_graph_from_modules
from jinja2.dependency import extract_dependencies
from jinja2.dependency import load_dependencies_from_module_dict
from jinja2.meta import find_referenced_templates
from jinja2.meta import find_undeclared_variables


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEMPLATES = {
    "base.html": "{% block content %}base{% endblock %}",
    "layout.html": (
        "{% extends 'base.html' %}"
        "{% block nav %}{% include 'nav.html' %}{% endblock %}"
    ),
    "nav.html": "{% from 'macros.html' import render_link %}nav",
    "macros.html": (
        "{% macro render_link(url, text) %}"
        "<a href='{{ url }}'>{{ text }}</a>"
        "{% endmacro %}"
    ),
    "page.html": (
        "{% extends 'layout.html' %}"
        "{% import 'macros.html' as m %}"
        "{% block content %}page{% endblock %}"
    ),
    "dynamic.html": "{% include tpl_var %}",
    "cycle_a.html": "{% extends 'cycle_b.html' %}",
    "cycle_b.html": "{% extends 'cycle_a.html' %}",
    "self_cycle.html": "{% include 'self_cycle.html' %}",
    "nested_if.html": (
        "{% if x %}{% include 'a.html' %}{% else %}{% include 'b.html' %}{% endif %}"
    ),
    "nested_for.html": (
        "{% for i in items %}{% include 'item.html' %}{% endfor %}"
    ),
    "nested_block.html": (
        "{% extends 'base.html' %}"
        "{% block content %}{% include 'widget.html' %}{% endblock %}"
    ),
    "nested_macro.html": (
        "{% macro wrapper() %}{% include 'inner.html' %}{% endmacro %}"
    ),
    "multi_include.html": "{% include ['a.html', 'b.html'] %}",
    "with_context.html": (
        "{% include 'ctx.html' with context %}"
        "{% import 'macros.html' as m with context %}"
    ),
    "ignore_missing.html": "{% include 'maybe.html' ignore missing %}",
    "from_import_alias.html": (
        "{% from 'macros.html' import render_link as link, render_card as card %}"
    ),
    "a.html": "a",
    "b.html": "b",
    "item.html": "item",
    "widget.html": "widget",
    "inner.html": "inner",
    "ctx.html": "ctx",
    "maybe.html": "maybe",
}


@pytest.fixture
def env():
    return Environment(loader=DictLoader(TEMPLATES))


@pytest.fixture
def graph(env):
    return build_dependency_graph(env)


# ---------------------------------------------------------------------------
# TestDependencyDataStructure
# ---------------------------------------------------------------------------


class TestDependencyDataStructure:
    def test_dependency_frozen(self):
        dep = Dependency(
            source="a.html",
            target="b.html",
            dep_type=DependencyType.EXTENDS,
            lineno=1,
        )
        assert dep.source == "a.html"
        assert dep.target == "b.html"
        assert dep.dep_type == DependencyType.EXTENDS
        assert dep.lineno == 1
        assert dep.with_context is False
        assert dep.ignore_missing is False
        assert dep.imported_names == ()
        assert dep.is_dynamic is False

    def test_dependency_hashable(self):
        dep = Dependency(
            source="a.html",
            target="b.html",
            dep_type=DependencyType.EXTENDS,
            lineno=1,
        )
        # Should be usable in a set
        s = {dep}
        assert dep in s

    def test_dependency_type_values(self):
        assert DependencyType.EXTENDS.value == "extends"
        assert DependencyType.INCLUDE.value == "include"
        assert DependencyType.IMPORT.value == "import"
        assert DependencyType.FROM_IMPORT.value == "from_import"

    def test_graph_add_and_query(self):
        g = DependencyGraph()
        dep = Dependency(
            source="a.html",
            target="b.html",
            dep_type=DependencyType.EXTENDS,
            lineno=1,
        )
        g.add_dependency(dep)
        assert "a.html" in g.nodes
        assert "b.html" in g.nodes
        assert g.get_dependencies("a.html") == [dep]
        assert g.get_dependencies("b.html") == []
        assert g.get_dependents("b.html") == [dep]
        assert g.get_dependents("a.html") == []

    def test_graph_filter_by_type(self):
        g = DependencyGraph()
        g.add_dependency(
            Dependency(
                source="a.html",
                target="b.html",
                dep_type=DependencyType.EXTENDS,
                lineno=1,
            )
        )
        g.add_dependency(
            Dependency(
                source="a.html",
                target="c.html",
                dep_type=DependencyType.INCLUDE,
                lineno=2,
            )
        )
        extends = g.get_dependencies("a.html", DependencyType.EXTENDS)
        assert len(extends) == 1
        assert extends[0].target == "b.html"

        includes = g.get_dependencies("a.html", DependencyType.INCLUDE)
        assert len(includes) == 1
        assert includes[0].target == "c.html"

    def test_graph_transitive_closure(self):
        g = DependencyGraph()
        g.add_dependency(
            Dependency(
                source="a", target="b", dep_type=DependencyType.EXTENDS, lineno=1
            )
        )
        g.add_dependency(
            Dependency(
                source="b", target="c", dep_type=DependencyType.EXTENDS, lineno=1
            )
        )
        g.add_dependency(
            Dependency(
                source="c", target="d", dep_type=DependencyType.INCLUDE, lineno=1
            )
        )
        all_deps = g.get_all_dependencies("a")
        assert all_deps == {"b", "c", "d"}

    def test_graph_transitive_closure_filtered(self):
        g = DependencyGraph()
        g.add_dependency(
            Dependency(
                source="a", target="b", dep_type=DependencyType.EXTENDS, lineno=1
            )
        )
        g.add_dependency(
            Dependency(
                source="b", target="c", dep_type=DependencyType.INCLUDE, lineno=1
            )
        )
        # Only follow EXTENDS edges
        extends_deps = g.get_all_dependencies("a", DependencyType.EXTENDS)
        assert extends_deps == {"b"}

    def test_graph_empty_query(self):
        g = DependencyGraph()
        assert g.get_dependencies("nonexistent") == []
        assert g.get_dependents("nonexistent") == []
        assert g.get_all_dependencies("nonexistent") == set()


# ---------------------------------------------------------------------------
# TestExtractDependencies
# ---------------------------------------------------------------------------


class TestExtractDependencies:
    def test_extends(self, env):
        ast = env.parse("{% extends 'base.html' %}", "test.html")
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 1
        assert deps[0].dep_type == DependencyType.EXTENDS
        assert deps[0].target == "base.html"
        assert deps[0].source == "test.html"
        assert deps[0].is_dynamic is False

    def test_include_basic(self, env):
        ast = env.parse("{% include 'header.html' %}", "test.html")
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 1
        assert deps[0].dep_type == DependencyType.INCLUDE
        assert deps[0].target == "header.html"

    def test_include_with_context(self, env):
        ast = env.parse("{% include 'ctx.html' with context %}", "test.html")
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 1
        assert deps[0].with_context is True

    def test_include_ignore_missing(self, env):
        ast = env.parse("{% include 'maybe.html' ignore missing %}", "test.html")
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 1
        assert deps[0].ignore_missing is True

    def test_import(self, env):
        ast = env.parse("{% import 'macros.html' as m %}", "test.html")
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 1
        assert deps[0].dep_type == DependencyType.IMPORT
        assert deps[0].target == "macros.html"

    def test_from_import(self, env):
        ast = env.parse("{% from 'macros.html' import render_link %}", "test.html")
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 1
        assert deps[0].dep_type == DependencyType.FROM_IMPORT
        assert deps[0].target == "macros.html"
        assert deps[0].imported_names == ("render_link",)

    def test_from_import_with_alias(self, env):
        ast = env.parse(
            "{% from 'macros.html' import render_link as link %}", "test.html"
        )
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 1
        assert deps[0].imported_names == (("render_link", "link"),)

    def test_from_import_multiple_names(self, env):
        ast = env.parse(
            "{% from 'macros.html' import render_link as link, render_card as card %}",
            "test.html",
        )
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 1
        assert deps[0].imported_names == (
            ("render_link", "link"),
            ("render_card", "card"),
        )

    def test_dynamic_template_ref(self, env):
        ast = env.parse("{% include tpl_var %}", "test.html")
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 1
        assert deps[0].target is None
        assert deps[0].is_dynamic is True

    def test_dynamic_extends(self, env):
        ast = env.parse("{% extends parent_tpl %}", "test.html")
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 1
        assert deps[0].target is None
        assert deps[0].is_dynamic is True
        assert deps[0].dep_type == DependencyType.EXTENDS

    def test_select_template_tuple(self, env):
        ast = env.parse("{% include ['a.html', 'b.html'] %}", "test.html")
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 2
        targets = {d.target for d in deps}
        assert targets == {"a.html", "b.html"}
        assert all(d.dep_type == DependencyType.INCLUDE for d in deps)

    def test_nested_in_if(self, env):
        ast = env.parse(
            "{% if x %}{% include 'a.html' %}{% else %}{% include 'b.html' %}{% endif %}",
            "test.html",
        )
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 2
        targets = {d.target for d in deps}
        assert targets == {"a.html", "b.html"}

    def test_nested_in_for(self, env):
        ast = env.parse(
            "{% for i in items %}{% include 'item.html' %}{% endfor %}",
            "test.html",
        )
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 1
        assert deps[0].target == "item.html"

    def test_nested_in_block(self, env):
        ast = env.parse(
            "{% extends 'base.html' %}"
            "{% block content %}{% include 'widget.html' %}{% endblock %}",
            "test.html",
        )
        deps = extract_dependencies(ast, "test.html")
        targets = {d.target for d in deps}
        assert "base.html" in targets
        assert "widget.html" in targets

    def test_nested_in_macro_body(self, env):
        ast = env.parse(
            "{% macro wrapper() %}{% include 'inner.html' %}{% endmacro %}",
            "test.html",
        )
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 1
        assert deps[0].target == "inner.html"

    def test_multiple_deps_same_template(self, env):
        src = (
            "{% extends 'base.html' %}"
            "{% include 'header.html' %}"
            "{% import 'macros.html' as m %}"
            "{% from 'utils.html' import helper %}"
        )
        ast = env.parse(src, "test.html")
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 4
        types = {d.dep_type for d in deps}
        assert types == {
            DependencyType.EXTENDS,
            DependencyType.INCLUDE,
            DependencyType.IMPORT,
            DependencyType.FROM_IMPORT,
        }

    def test_lineno_tracking(self, env):
        src = "{% extends 'base.html' %}\n{% include 'header.html' %}"
        ast = env.parse(src, "test.html")
        deps = extract_dependencies(ast, "test.html")
        extends_dep = next(d for d in deps if d.dep_type == DependencyType.EXTENDS)
        include_dep = next(d for d in deps if d.dep_type == DependencyType.INCLUDE)
        assert extends_dep.lineno == 1
        assert include_dep.lineno == 2

    def test_no_deps(self, env):
        ast = env.parse("Hello {{ name }}!", "test.html")
        deps = extract_dependencies(ast, "test.html")
        assert deps == []

    def test_conditional_extends(self, env):
        src = "{% if use_alt %}{% extends 'alt.html' %}{% else %}{% extends 'base.html' %}{% endif %}"
        ast = env.parse(src, "test.html")
        deps = extract_dependencies(ast, "test.html")
        extends_deps = [d for d in deps if d.dep_type == DependencyType.EXTENDS]
        assert len(extends_deps) == 2
        targets = {d.target for d in extends_deps}
        assert targets == {"alt.html", "base.html"}

    def test_dynamic_in_tuple(self, env):
        """A tuple with mixed static and dynamic refs."""
        ast = env.parse('{% include ["a.html", var, "b.html"] %}', "test.html")
        deps = extract_dependencies(ast, "test.html")
        assert len(deps) == 3
        static_targets = {d.target for d in deps if not d.is_dynamic}
        dynamic_deps = [d for d in deps if d.is_dynamic]
        assert static_targets == {"a.html", "b.html"}
        assert len(dynamic_deps) == 1


# ---------------------------------------------------------------------------
# TestBuildDependencyGraph
# ---------------------------------------------------------------------------


class TestBuildDependencyGraph:
    def test_linear_chain(self, env):
        graph = build_dependency_graph(env, ["page.html"])
        # page.html -> layout.html -> base.html
        # page.html -> layout.html (nav.html -> macros.html)
        # page.html -> macros.html
        assert "page.html" in graph.nodes
        assert "layout.html" in graph.nodes
        assert "base.html" in graph.nodes
        assert "macros.html" in graph.nodes
        assert "nav.html" in graph.nodes

    def test_diamond_dependency(self):
        """Two templates that both depend on the same base."""
        templates = {
            "base": "base",
            "left": "{% extends 'base' %}",
            "right": "{% extends 'base' %}",
            "top": "{% include 'left' %}{% include 'right' %}",
        }
        env = Environment(loader=DictLoader(templates))
        graph = build_dependency_graph(env, ["top"])
        assert "base" in graph.nodes
        assert graph.get_dependencies("base") == []
        base_dependents = graph.get_dependents("base")
        assert len(base_dependents) == 2

    def test_full_scan(self, env):
        """Scan all templates (no root_templates argument)."""
        graph = build_dependency_graph(env)
        # All templates in TEMPLATES dict should be in the graph
        for name in TEMPLATES:
            assert name in graph.nodes

    def test_missing_template_graceful(self):
        """Referencing a non-existent template should not crash."""
        templates = {
            "page": "{% include 'nonexistent.html' %}",
        }
        env = Environment(loader=DictLoader(templates))
        graph = build_dependency_graph(env, ["page"])
        assert "page" in graph.nodes
        assert "nonexistent.html" in graph.nodes
        deps = graph.get_dependencies("page")
        assert len(deps) == 1
        assert deps[0].target == "nonexistent.html"

    def test_transitive_closure(self, env):
        graph = build_dependency_graph(env, ["page.html"])
        all_deps = graph.get_all_dependencies("page.html")
        assert "layout.html" in all_deps
        assert "base.html" in all_deps
        assert "nav.html" in all_deps
        assert "macros.html" in all_deps


# ---------------------------------------------------------------------------
# TestCycleDetection
# ---------------------------------------------------------------------------


class TestCycleDetection:
    def test_direct_cycle(self):
        templates = {
            "a": "{% extends 'b' %}",
            "b": "{% extends 'a' %}",
        }
        env = Environment(loader=DictLoader(templates))
        graph = build_dependency_graph(env, ["a"])
        cycles = graph.cycles
        assert len(cycles) >= 1
        # At least one cycle should contain both 'a' and 'b'
        found = False
        for cycle in cycles:
            if "a" in cycle and "b" in cycle:
                found = True
                break
        assert found, f"Expected cycle with a and b, got {cycles}"

    def test_indirect_cycle(self):
        templates = {
            "a": "{% extends 'b' %}",
            "b": "{% extends 'c' %}",
            "c": "{% extends 'a' %}",
        }
        env = Environment(loader=DictLoader(templates))
        graph = build_dependency_graph(env, ["a"])
        assert len(graph.cycles) >= 1
        assert graph.has_cycle("a")
        assert graph.has_cycle("b")
        assert graph.has_cycle("c")

    def test_self_cycle(self):
        templates = {
            "self_ref": "{% include 'self_ref' %}",
        }
        env = Environment(loader=DictLoader(templates))
        graph = build_dependency_graph(env, ["self_ref"])
        assert len(graph.cycles) >= 1
        assert graph.has_cycle("self_ref")

    def test_no_cycle(self):
        templates = {
            "a": "{% extends 'b' %}",
            "b": "{% extends 'c' %}",
            "c": "",
        }
        env = Environment(loader=DictLoader(templates))
        graph = build_dependency_graph(env, ["a"])
        assert graph.cycles == []
        assert not graph.has_cycle("a")
        assert not graph.has_cycle("b")
        assert not graph.has_cycle("c")

    def test_cycle_nodes_marked(self):
        templates = {
            "clean": "{% include 'x' %}",
            "x": "x",
            "cyc_a": "{% include 'cyc_b' %}",
            "cyc_b": "{% include 'cyc_a' %}",
        }
        env = Environment(loader=DictLoader(templates))
        graph = build_dependency_graph(env)
        assert graph.has_cycle("cyc_a")
        assert graph.has_cycle("cyc_b")
        assert not graph.has_cycle("clean")
        assert not graph.has_cycle("x")

    def test_topological_sort_with_cycle(self):
        templates = {
            "a": "{% extends 'b' %}",
            "b": "{% extends 'a' %}",
        }
        env = Environment(loader=DictLoader(templates))
        graph = build_dependency_graph(env)
        assert graph.topological_sort() is None

    def test_topological_sort_without_cycle(self):
        templates = {
            "base": "",
            "mid": "{% extends 'base' %}",
            "top": "{% extends 'mid' %}",
        }
        env = Environment(loader=DictLoader(templates))
        graph = build_dependency_graph(env)
        order = graph.topological_sort()
        assert order is not None
        # base must come before mid, mid before top
        assert order.index("base") < order.index("mid")
        assert order.index("mid") < order.index("top")

    def test_multiple_cycles(self):
        templates = {
            "a": "{% include 'b' %}",
            "b": "{% include 'a' %}",
            "c": "{% include 'd' %}",
            "d": "{% include 'c' %}",
        }
        env = Environment(loader=DictLoader(templates))
        graph = build_dependency_graph(env)
        assert len(graph.cycles) >= 2
        assert graph.has_cycle("a")
        assert graph.has_cycle("c")


# ---------------------------------------------------------------------------
# TestPrecompiledMetadata
# ---------------------------------------------------------------------------


class TestPrecompiledMetadata:
    def test_metadata_in_compiled_output(self, env):
        """Compiled source code (raw=True) should contain _jinja_dependency_meta."""
        source = "{% extends 'base.html' %}{% include 'header.html' %}"
        code = env.compile(source, "test.html", raw=True)
        assert "_jinja_dependency_meta" in code
        assert "'extends'" in code
        assert "'base.html'" in code
        assert "'include'" in code
        assert "'header.html'" in code

    def test_metadata_empty_template(self, env):
        """A template with no dependencies should have an empty meta list."""
        code = env.compile("Hello {{ name }}", "test.html", raw=True)
        assert "_jinja_dependency_meta = []" in code

    def test_roundtrip_ast_vs_module(self, env):
        """Dependencies extracted from AST should match those read from
        the compiled module."""
        source = (
            "{% extends 'base.html' %}"
            "{% from 'macros.html' import render_link as link %}"
            "{% include 'footer.html' ignore missing %}"
        )
        template_name = "roundtrip.html"

        # From AST
        ast = env.parse(source, template_name)
        ast_deps = extract_dependencies(ast, template_name)

        # From compiled module — use defer_init=True (same as
        # compile_templates does) so the generated code is importable
        # without an environment variable at module level.
        code_str = env.compile(source, template_name, raw=True, defer_init=True)
        mod_ns: dict = {}
        exec(compile(code_str, "<test>", "exec"), mod_ns)
        module_deps = load_dependencies_from_module_dict(template_name, mod_ns)

        # Compare
        assert len(ast_deps) == len(module_deps)
        for a, m in zip(ast_deps, module_deps):
            assert a.source == m.source
            assert a.target == m.target
            assert a.dep_type == m.dep_type
            assert a.lineno == m.lineno
            assert a.with_context == m.with_context
            assert a.ignore_missing == m.ignore_missing
            assert a.imported_names == m.imported_names
            assert a.is_dynamic == m.is_dynamic

    def test_build_graph_from_modules(self, env):
        """build_dependency_graph_from_modules should produce the same graph
        structure as build_dependency_graph from source."""
        templates = {
            "base": "",
            "page": "{% extends 'base' %}{% include 'widget' %}",
            "widget": "w",
        }
        src_env = Environment(loader=DictLoader(templates))

        # Source-based graph
        src_graph = build_dependency_graph(src_env, ["page"])

        # Module-based graph — use defer_init=True for importable modules
        module_dicts = {}
        for name, source in templates.items():
            code_str = src_env.compile(source, name, raw=True, defer_init=True)
            mod_ns: dict = {}
            exec(compile(code_str, "<test>", "exec"), mod_ns)
            module_dicts[name] = mod_ns

        mod_graph = build_dependency_graph_from_modules(module_dicts)

        # Compare nodes
        assert src_graph.nodes == mod_graph.nodes

        # Compare edges
        for node in src_graph.nodes:
            src_deps = src_graph.get_dependencies(node)
            mod_deps = mod_graph.get_dependencies(node)
            assert len(src_deps) == len(mod_deps)
            for s, m in zip(
                sorted(src_deps, key=lambda d: (d.target or "", d.dep_type.value)),
                sorted(mod_deps, key=lambda d: (d.target or "", d.dep_type.value)),
            ):
                assert s.target == m.target
                assert s.dep_type == m.dep_type

    def test_module_loader_get_meta(self, tmp_path):
        """ModuleLoader.get_dependency_meta should return metadata."""
        templates = {
            "base": "base content",
            "page": "{% extends 'base' %}{% include 'widget' %}",
            "widget": "widget content",
        }
        env = Environment(loader=DictLoader(templates))
        env.compile_templates(tmp_path, zip=None)

        from jinja2.loaders import ModuleLoader

        mod_loader = ModuleLoader(str(tmp_path))
        meta = mod_loader.get_dependency_meta("page")
        assert len(meta) == 2
        types = {item["type"] for item in meta}
        assert "extends" in types
        assert "include" in types

    def test_module_loader_get_meta_missing(self, tmp_path):
        """ModuleLoader.get_dependency_meta should return [] for missing templates."""
        from jinja2.loaders import ModuleLoader

        mod_loader = ModuleLoader(str(tmp_path))
        meta = mod_loader.get_dependency_meta("nonexistent")
        assert meta == []


# ---------------------------------------------------------------------------
# TestEnvironmentAPI
# ---------------------------------------------------------------------------


class TestEnvironmentAPI:
    def test_get_template_dependencies(self, env):
        deps = env.get_template_dependencies("page.html")
        assert len(deps) >= 2
        types = {d.dep_type for d in deps}
        assert DependencyType.EXTENDS in types
        assert DependencyType.IMPORT in types

    def test_get_dependency_graph(self, env):
        graph = env.get_dependency_graph(["page.html"])
        assert isinstance(graph, DependencyGraph)
        assert "page.html" in graph.nodes
        assert "base.html" in graph.nodes

    def test_get_dependency_graph_all(self, env):
        graph = env.get_dependency_graph()
        # Should include all templates from the loader
        for name in TEMPLATES:
            assert name in graph.nodes

    def test_get_template_dependencies_no_loader(self):
        env = Environment()
        with pytest.raises(TypeError, match="no loader"):
            env.get_template_dependencies("foo.html")


# ---------------------------------------------------------------------------
# TestBackwardCompatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_find_referenced_templates_unchanged(self, env):
        """The old find_referenced_templates should still work."""
        ast = env.parse(
            "{% extends 'layout.html' %}{% include helper %}",
            "test.html",
        )
        refs = list(find_referenced_templates(ast))
        assert refs == ["layout.html", None]

    def test_find_undeclared_variables_unchanged(self, env):
        """The old find_undeclared_variables should still work."""
        ast = env.parse("{% set foo = 42 %}{{ bar + foo }}")
        undeclared = find_undeclared_variables(ast)
        assert undeclared == {"bar"}

    def test_rendering_unchanged(self, env):
        """Compiled templates with dependency metadata should render normally."""
        env2 = Environment(
            loader=DictLoader(
                {
                    "base": "|{% block content %}base{% endblock %}|",
                    "child": "{% extends 'base' %}{% block content %}child{% endblock %}",
                }
            )
        )
        result = env2.get_template("child").render()
        assert result == "|child|"

    def test_rendering_with_imports(self, env):
        """Templates with imports should render correctly after metadata embedding."""
        env2 = Environment(
            loader=DictLoader(
                {
                    "macros": "{% macro hello(name) %}Hello {{ name }}{% endmacro %}",
                    "page": (
                        "{% from 'macros' import hello %}{{ hello('World') }}"
                    ),
                }
            )
        )
        result = env2.get_template("page").render()
        assert result == "Hello World"

    def test_compile_templates_to_dir(self, tmp_path):
        """compile_templates should still work and produce renderable modules."""
        templates = {
            "base": "base: {% block x %}default{% endblock %}",
            "child": "{% extends 'base' %}{% block x %}override{% endblock %}",
        }
        env = Environment(loader=DictLoader(templates))
        env.compile_templates(tmp_path, zip=None)

        from jinja2.loaders import ModuleLoader

        mod_env = Environment(loader=ModuleLoader(str(tmp_path)))
        result = mod_env.get_template("child").render()
        assert result == "base: override"
