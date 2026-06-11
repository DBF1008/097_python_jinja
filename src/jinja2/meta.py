"""Functions that expose information about templates that might be
interesting for introspection.
"""

import typing as t
from dataclasses import dataclass

from . import nodes
from .compiler import CodeGenerator
from .compiler import Frame
from .exceptions import TemplateNotFound
from .visitor import NodeVisitor

if t.TYPE_CHECKING:
    from .environment import Environment


class TrackingCodeGenerator(CodeGenerator):
    """We abuse the code generator for introspection."""

    def __init__(self, environment: "Environment") -> None:
        super().__init__(environment, "<introspection>", "<introspection>")
        self.undeclared_identifiers: set[str] = set()

    def write(self, x: str) -> None:
        """Don't write."""

    def enter_frame(self, frame: Frame) -> None:
        """Remember all undeclared identifiers."""
        super().enter_frame(frame)

        for _, (action, param) in frame.symbols.loads.items():
            if action == "resolve" and param not in self.environment.globals:
                self.undeclared_identifiers.add(param)


def find_undeclared_variables(ast: nodes.Template) -> set[str]:
    """Returns a set of all variables in the AST that will be looked up from
    the context at runtime.  Because at compile time it's not known which
    variables will be used depending on the path the execution takes at
    runtime, all variables are returned.

    >>> from jinja2 import Environment, meta
    >>> env = Environment()
    >>> ast = env.parse('{% set foo = 42 %}{{ bar + foo }}')
    >>> meta.find_undeclared_variables(ast) == {'bar'}
    True

    .. admonition:: Implementation

       Internally the code generator is used for finding undeclared variables.
       This is good to know because the code generator might raise a
       :exc:`TemplateAssertionError` during compilation and as a matter of
       fact this function can currently raise that exception as well.
    """
    codegen = TrackingCodeGenerator(ast.environment)  # type: ignore
    codegen.visit(ast)
    return codegen.undeclared_identifiers


_ref_types = (nodes.Extends, nodes.FromImport, nodes.Import, nodes.Include)
_RefType = nodes.Extends | nodes.FromImport | nodes.Import | nodes.Include


def find_referenced_templates(ast: nodes.Template) -> t.Iterator[str | None]:
    """Finds all the referenced templates from the AST.  This will return an
    iterator over all the hardcoded template extensions, inclusions and
    imports.  If dynamic inheritance or inclusion is used, `None` will be
    yielded.

    >>> from jinja2 import Environment, meta
    >>> env = Environment()
    >>> ast = env.parse('{% extends "layout.html" %}{% include helper %}')
    >>> list(meta.find_referenced_templates(ast))
    ['layout.html', None]

    This function is useful for dependency tracking.  For example if you want
    to rebuild parts of the website after a layout template has changed.
    """
    template_name: t.Any

    for node in ast.find_all(_ref_types):
        template: nodes.Expr = node.template  # type: ignore

        if not isinstance(template, nodes.Const):
            # a tuple with some non consts in there
            if isinstance(template, (nodes.Tuple, nodes.List)):
                for template_name in template.items:
                    # something const, only yield the strings and ignore
                    # non-string consts that really just make no sense
                    if isinstance(template_name, nodes.Const):
                        if isinstance(template_name.value, str):
                            yield template_name.value
                    # something dynamic in there
                    else:
                        yield None
            # something dynamic we don't know about here
            else:
                yield None
            continue
        # constant is a basestring, direct template name
        if isinstance(template.value, str):
            yield template.value
        # a tuple or list (latter *should* not happen) made of consts,
        # yield the consts that are strings.  We could warn here for
        # non string values
        elif isinstance(node, nodes.Include) and isinstance(
            template.value, (tuple, list)
        ):
            for template_name in template.value:
                if isinstance(template_name, str):
                    yield template_name
        # something else we don't care about, we could warn here
        else:
            yield None


@dataclass(frozen=True)
class TemplateDependency:
    """A single dependency edge from one template to another.

    .. versionadded:: 3.2
    """

    name: str | None
    type: str
    imported_names: tuple[str | tuple[str, str], ...] | None = None
    alias: str | None = None
    ignore_missing: bool = False


@dataclass
class DependencyGraph:
    """The full dependency graph rooted at a template, with cycle detection.

    .. versionadded:: 3.2
    """

    dependencies: dict[str, list[TemplateDependency]]
    cycles: list[tuple[str, ...]]


def _extract_template_names(
    template_expr: nodes.Expr,
) -> list[str | None]:
    if isinstance(template_expr, nodes.Const):
        if isinstance(template_expr.value, str):
            return [template_expr.value]
        if isinstance(template_expr.value, (tuple, list)):
            return [
                v if isinstance(v, str) else None
                for v in template_expr.value
            ]
        return [None]
    if isinstance(template_expr, (nodes.Tuple, nodes.List)):
        result: list[str | None] = []
        for item in template_expr.items:
            if isinstance(item, nodes.Const) and isinstance(item.value, str):
                result.append(item.value)
            else:
                result.append(None)
        return result
    return [None]


class _DependencyFinder(NodeVisitor):
    def __init__(self) -> None:
        self.dependencies: list[TemplateDependency] = []

    def visit_Extends(self, node: nodes.Extends) -> None:
        for name in _extract_template_names(node.template):
            self.dependencies.append(
                TemplateDependency(name=name, type="extends")
            )

    def visit_Include(self, node: nodes.Include) -> None:
        for name in _extract_template_names(node.template):
            self.dependencies.append(
                TemplateDependency(
                    name=name,
                    type="includes",
                    ignore_missing=node.ignore_missing,
                )
            )

    def visit_Import(self, node: nodes.Import) -> None:
        for name in _extract_template_names(node.template):
            self.dependencies.append(
                TemplateDependency(
                    name=name, type="imports", alias=node.target
                )
            )

    def visit_FromImport(self, node: nodes.FromImport) -> None:
        for name in _extract_template_names(node.template):
            self.dependencies.append(
                TemplateDependency(
                    name=name,
                    type="from_imports",
                    imported_names=tuple(node.names),
                )
            )


def find_template_dependencies(
    ast: nodes.Template,
) -> list[TemplateDependency]:
    """Return the direct dependencies of a parsed template AST, classified
    by type (extends, includes, imports, from_imports).

    Unlike :func:`find_referenced_templates`, this distinguishes the kind of
    each reference and captures metadata such as imported names and aliases.
    Dynamic references (non-constant template expressions) are included with
    ``name`` set to ``None``.

    .. versionadded:: 3.2
    """
    finder = _DependencyFinder()
    finder.visit(ast)
    return finder.dependencies


def build_dependency_graph(
    environment: "Environment", template_name: str
) -> DependencyGraph:
    """Recursively resolve template references starting from
    *template_name* and return a :class:`DependencyGraph`.

    Templates are parsed via the environment's loader but never executed.
    Circular dependencies are detected and recorded in
    :attr:`DependencyGraph.cycles` rather than raising an error.

    .. versionadded:: 3.2
    """
    dependencies: dict[str, list[TemplateDependency]] = {}
    cycles: list[tuple[str, ...]] = []
    gray: set[str] = set()
    black: set[str] = set()

    def _visit(name: str, path: list[str]) -> None:
        if name in black:
            return
        if name in gray:
            cycle_start = path.index(name)
            cycles.append(tuple(path[cycle_start:]))
            return

        gray.add(name)
        path.append(name)

        try:
            source = environment.loader.get_source(environment, name)[0]  # type: ignore[union-attr]
            ast = environment.parse(source, name)
        except TemplateNotFound:
            dependencies[name] = []
            gray.discard(name)
            black.add(name)
            path.pop()
            return

        deps = find_template_dependencies(ast)
        dependencies[name] = deps

        for dep in deps:
            if dep.name is not None:
                _visit(dep.name, path)

        path.pop()
        gray.discard(name)
        black.add(name)

    _visit(template_name, [])
    return DependencyGraph(dependencies=dependencies, cycles=cycles)
