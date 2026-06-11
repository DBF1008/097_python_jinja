import pytest

from jinja2 import DictLoader
from jinja2 import Environment
from jinja2 import meta
from jinja2.meta import DependencyGraph
from jinja2.meta import TemplateDependency


class TestFindTemplateDependencies:
    def test_extends(self):
        env = Environment()
        ast = env.parse('{% extends "base.html" %}')
        deps = meta.find_template_dependencies(ast)
        assert len(deps) == 1
        assert deps[0] == TemplateDependency(name="base.html", type="extends")

    def test_include(self):
        env = Environment()
        ast = env.parse('{% include "header.html" %}')
        deps = meta.find_template_dependencies(ast)
        assert len(deps) == 1
        assert deps[0] == TemplateDependency(
            name="header.html", type="includes"
        )

    def test_include_ignore_missing(self):
        env = Environment()
        ast = env.parse('{% include "header.html" ignore missing %}')
        deps = meta.find_template_dependencies(ast)
        assert len(deps) == 1
        assert deps[0].ignore_missing is True

    def test_import(self):
        env = Environment()
        ast = env.parse('{% import "macros.html" as m %}')
        deps = meta.find_template_dependencies(ast)
        assert len(deps) == 1
        assert deps[0] == TemplateDependency(
            name="macros.html", type="imports", alias="m"
        )

    def test_from_import(self):
        env = Environment()
        ast = env.parse('{% from "forms.html" import input, textarea as ta %}')
        deps = meta.find_template_dependencies(ast)
        assert len(deps) == 1
        assert deps[0].type == "from_imports"
        assert deps[0].name == "forms.html"
        assert deps[0].imported_names == ("input", ("textarea", "ta"))

    def test_multiple_dependencies(self):
        env = Environment()
        ast = env.parse(
            '{% extends "layout.html" %}'
            '{% block body %}'
            '{% include "header.html" %}'
            '{% import "macros.html" as m %}'
            '{% from "forms.html" import field %}'
            '{% endblock %}'
        )
        deps = meta.find_template_dependencies(ast)
        assert len(deps) == 4
        assert deps[0].type == "extends"
        assert deps[0].name == "layout.html"
        assert deps[1].type == "includes"
        assert deps[1].name == "header.html"
        assert deps[2].type == "imports"
        assert deps[2].name == "macros.html"
        assert deps[3].type == "from_imports"
        assert deps[3].name == "forms.html"

    def test_dynamic_extends(self):
        env = Environment()
        ast = env.parse("{% extends base %}")
        deps = meta.find_template_dependencies(ast)
        assert len(deps) == 1
        assert deps[0].name is None
        assert deps[0].type == "extends"

    def test_dynamic_include(self):
        env = Environment()
        ast = env.parse("{% include tmpl %}")
        deps = meta.find_template_dependencies(ast)
        assert len(deps) == 1
        assert deps[0].name is None
        assert deps[0].type == "includes"

    def test_include_list(self):
        env = Environment()
        ast = env.parse('{% include ["a.html", "b.html"] %}')
        deps = meta.find_template_dependencies(ast)
        assert len(deps) == 2
        assert deps[0].name == "a.html"
        assert deps[1].name == "b.html"
        assert all(d.type == "includes" for d in deps)

    def test_include_tuple(self):
        env = Environment()
        ast = env.parse('{% include ("a.html", "b.html") %}')
        deps = meta.find_template_dependencies(ast)
        assert len(deps) == 2
        assert deps[0].name == "a.html"
        assert deps[1].name == "b.html"

    def test_include_list_with_dynamic(self):
        env = Environment()
        ast = env.parse('{% include ["a.html", x, "b.html"] %}')
        deps = meta.find_template_dependencies(ast)
        assert len(deps) == 3
        assert deps[0].name == "a.html"
        assert deps[1].name is None
        assert deps[2].name == "b.html"

    def test_no_dependencies(self):
        env = Environment()
        ast = env.parse("{{ foo }}")
        deps = meta.find_template_dependencies(ast)
        assert deps == []

    def test_dependency_inside_control_flow(self):
        env = Environment()
        ast = env.parse(
            '{% if show_header %}{% include "header.html" %}{% endif %}'
            '{% for item in items %}{% include "item.html" %}{% endfor %}'
        )
        deps = meta.find_template_dependencies(ast)
        assert len(deps) == 2
        assert deps[0].name == "header.html"
        assert deps[1].name == "item.html"

    def test_from_import_simple_names(self):
        env = Environment()
        ast = env.parse('{% from "m.html" import a, b, c %}')
        deps = meta.find_template_dependencies(ast)
        assert deps[0].imported_names == ("a", "b", "c")

    def test_from_import_all_aliased(self):
        env = Environment()
        ast = env.parse('{% from "m.html" import a as x, b as y %}')
        deps = meta.find_template_dependencies(ast)
        assert deps[0].imported_names == (("a", "x"), ("b", "y"))


class TestBuildDependencyGraph:
    def test_simple_extends_chain(self):
        env = Environment(
            loader=DictLoader(
                {
                    "child.html": '{% extends "parent.html" %}',
                    "parent.html": '{% extends "base.html" %}',
                    "base.html": "base content",
                }
            )
        )
        graph = meta.build_dependency_graph(env, "child.html")
        assert "child.html" in graph.dependencies
        assert "parent.html" in graph.dependencies
        assert "base.html" in graph.dependencies
        assert graph.dependencies["child.html"][0].name == "parent.html"
        assert graph.dependencies["parent.html"][0].name == "base.html"
        assert graph.dependencies["base.html"] == []
        assert graph.cycles == []

    def test_include_and_import_mix(self):
        env = Environment(
            loader=DictLoader(
                {
                    "page.html": (
                        '{% extends "layout.html" %}'
                        '{% block body %}'
                        '{% include "nav.html" %}'
                        '{% from "macros.html" import btn %}'
                        '{% endblock %}'
                    ),
                    "layout.html": "layout",
                    "nav.html": "nav",
                    "macros.html": "{% macro btn() %}btn{% endmacro %}",
                }
            )
        )
        graph = meta.build_dependency_graph(env, "page.html")
        assert len(graph.dependencies) == 4
        page_deps = graph.dependencies["page.html"]
        assert page_deps[0].type == "extends"
        assert page_deps[1].type == "includes"
        assert page_deps[2].type == "from_imports"
        assert graph.cycles == []

    def test_diamond_dependency(self):
        env = Environment(
            loader=DictLoader(
                {
                    "a.html": (
                        '{% include "b.html" %}{% include "c.html" %}'
                    ),
                    "b.html": '{% include "c.html" %}',
                    "c.html": "leaf",
                }
            )
        )
        graph = meta.build_dependency_graph(env, "a.html")
        assert len(graph.dependencies) == 3
        assert graph.dependencies["c.html"] == []
        assert graph.cycles == []

    def test_dynamic_ref_not_resolved(self):
        env = Environment(
            loader=DictLoader(
                {
                    "page.html": "{% include tmpl %}",
                }
            )
        )
        graph = meta.build_dependency_graph(env, "page.html")
        assert len(graph.dependencies) == 1
        assert graph.dependencies["page.html"][0].name is None

    def test_missing_template(self):
        env = Environment(
            loader=DictLoader(
                {
                    "page.html": '{% extends "nonexistent.html" %}',
                }
            )
        )
        graph = meta.build_dependency_graph(env, "page.html")
        assert "nonexistent.html" in graph.dependencies
        assert graph.dependencies["nonexistent.html"] == []

    def test_multiple_includes_same_template(self):
        env = Environment(
            loader=DictLoader(
                {
                    "page.html": (
                        '{% include "widget.html" %}'
                        '{% include "widget.html" %}'
                    ),
                    "widget.html": "widget",
                }
            )
        )
        graph = meta.build_dependency_graph(env, "page.html")
        page_deps = graph.dependencies["page.html"]
        assert len(page_deps) == 2
        assert len(graph.dependencies) == 2

    def test_include_list_in_graph(self):
        env = Environment(
            loader=DictLoader(
                {
                    "page.html": '{% include ["a.html", "b.html"] %}',
                    "a.html": "a",
                    "b.html": "b",
                }
            )
        )
        graph = meta.build_dependency_graph(env, "page.html")
        assert len(graph.dependencies) == 3
        assert "a.html" in graph.dependencies
        assert "b.html" in graph.dependencies

    def test_both_entry_points_agree(self):
        env = Environment(
            loader=DictLoader(
                {
                    "page.html": (
                        '{% extends "base.html" %}'
                        '{% block body %}'
                        '{% from "macros.html" import btn %}'
                        '{% endblock %}'
                    ),
                    "base.html": "base",
                    "macros.html": "{% macro btn() %}b{% endmacro %}",
                }
            )
        )
        source = env.loader.get_source(env, "page.html")[0]
        ast = env.parse(source, "page.html")
        direct_deps = meta.find_template_dependencies(ast)

        graph = meta.build_dependency_graph(env, "page.html")
        assert graph.dependencies["page.html"] == direct_deps


class TestCycleDetection:
    def test_simple_cycle(self):
        env = Environment(
            loader=DictLoader(
                {
                    "a.html": '{% include "b.html" %}',
                    "b.html": '{% include "a.html" %}',
                }
            )
        )
        graph = meta.build_dependency_graph(env, "a.html")
        assert len(graph.cycles) == 1
        assert graph.cycles[0] == ("a.html", "b.html")
        assert "a.html" in graph.dependencies
        assert "b.html" in graph.dependencies

    def test_extends_cycle(self):
        env = Environment(
            loader=DictLoader(
                {
                    "a.html": '{% extends "b.html" %}',
                    "b.html": '{% extends "a.html" %}',
                }
            )
        )
        graph = meta.build_dependency_graph(env, "a.html")
        assert len(graph.cycles) == 1
        assert graph.cycles[0] == ("a.html", "b.html")

    def test_three_node_cycle(self):
        env = Environment(
            loader=DictLoader(
                {
                    "a.html": '{% include "b.html" %}',
                    "b.html": '{% include "c.html" %}',
                    "c.html": '{% include "a.html" %}',
                }
            )
        )
        graph = meta.build_dependency_graph(env, "a.html")
        assert len(graph.cycles) == 1
        assert graph.cycles[0] == ("a.html", "b.html", "c.html")

    def test_cycle_with_branch(self):
        env = Environment(
            loader=DictLoader(
                {
                    "a.html": (
                        '{% include "b.html" %}{% include "c.html" %}'
                    ),
                    "b.html": '{% include "a.html" %}',
                    "c.html": "no deps",
                }
            )
        )
        graph = meta.build_dependency_graph(env, "a.html")
        assert len(graph.cycles) == 1
        assert graph.cycles[0] == ("a.html", "b.html")
        assert graph.dependencies["c.html"] == []

    def test_self_referential(self):
        env = Environment(
            loader=DictLoader(
                {
                    "a.html": '{% include "a.html" %}',
                }
            )
        )
        graph = meta.build_dependency_graph(env, "a.html")
        assert len(graph.cycles) == 1
        assert graph.cycles[0] == ("a.html",)

    def test_cycle_does_not_prevent_full_resolution(self):
        env = Environment(
            loader=DictLoader(
                {
                    "a.html": (
                        '{% include "b.html" %}{% include "d.html" %}'
                    ),
                    "b.html": '{% include "c.html" %}',
                    "c.html": '{% include "a.html" %}',
                    "d.html": "leaf",
                }
            )
        )
        graph = meta.build_dependency_graph(env, "a.html")
        assert len(graph.dependencies) == 4
        assert graph.dependencies["d.html"] == []
        assert len(graph.cycles) == 1
