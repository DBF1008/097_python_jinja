"""Structured dependency analysis for Jinja2 templates.

This module provides tools for analyzing template dependencies without
executing templates. It supports:

- Extracting typed dependencies (extends, include, import, from-import)
  from template ASTs
- Building complete dependency graphs via BFS traversal
- Detecting circular dependencies
- Reading dependency metadata from precompiled template modules

Two entry points are provided:

1. :func:`build_dependency_graph` — from an Environment with a loader
   (source-based analysis)
2. :func:`build_dependency_graph_from_modules` — from precompiled module
   dictionaries (artifact-based analysis)

Both produce identical :class:`DependencyGraph` results.

.. versionadded:: 3.2
"""

from __future__ import annotations

import enum
import typing as t
from dataclasses import dataclass
from dataclasses import field

from . import nodes
from .visitor import NodeVisitor

if t.TYPE_CHECKING:
    from .environment import Environment


class DependencyType(enum.Enum):
    """The type of dependency relationship between templates."""

    EXTENDS = "extends"
    INCLUDE = "include"
    IMPORT = "import"
    FROM_IMPORT = "from_import"


# Map node types to dependency types
_NODE_TYPE_MAP: dict[type, DependencyType] = {
    nodes.Extends: DependencyType.EXTENDS,
    nodes.Include: DependencyType.INCLUDE,
    nodes.Import: DependencyType.IMPORT,
    nodes.FromImport: DependencyType.FROM_IMPORT,
}

# Tuple of node types for find_all()
_REF_TYPES = (nodes.Extends, nodes.FromImport, nodes.Import, nodes.Include)


@dataclass(frozen=True)
class Dependency:
    """A single dependency edge from one template to another.

    Attributes:
        source: The name of the source (depending) template.
        target: The name of the target (depended-upon) template.
            ``None`` when the template reference is dynamic.
        dep_type: The kind of dependency relationship.
        lineno: The line number in the source template where the
            dependency is declared.
        with_context: Whether the include or import uses
            ``with context``.
        ignore_missing: Whether an include uses ``ignore missing``.
        imported_names: For from-import dependencies, the list of names
            being imported (possibly as ``(name, alias)`` tuples).
        is_dynamic: Whether the template reference is dynamic (a variable
            rather than a string literal).
    """

    source: str
    target: str | None
    dep_type: DependencyType
    lineno: int
    with_context: bool = False
    ignore_missing: bool = False
    imported_names: tuple[t.Union[str, tuple[str, str]], ...] = ()
    is_dynamic: bool = False

    def __hash__(self) -> int:
        return hash((self.source, self.target, self.dep_type, self.lineno))


@dataclass
class DependencyGraph:
    """A complete template dependency graph with cycle detection.

    Attributes:
        nodes: All known template names in the graph.
        edges: Forward adjacency list mapping each template to its
            direct dependencies.
        reverse_edges: Reverse adjacency list mapping each template to
            the dependencies that point to it.
        cycles: Detected cycles as tuples of template names. Each tuple
            represents one cycle path (the last element equals the first).
    """

    nodes: set[str] = field(default_factory=set)
    edges: dict[str, list[Dependency]] = field(default_factory=dict)
    reverse_edges: dict[str, list[Dependency]] = field(default_factory=dict)
    cycles: list[tuple[str, ...]] = field(default_factory=list)
    _cycle_nodes: set[str] = field(default_factory=set, repr=False)

    def add_dependency(self, dep: Dependency) -> None:
        """Add a dependency edge to the graph."""
        self.nodes.add(dep.source)
        if dep.target is not None:
            self.nodes.add(dep.target)
        self.edges.setdefault(dep.source, []).append(dep)
        if dep.target is not None:
            self.reverse_edges.setdefault(dep.target, []).append(dep)

    def get_dependencies(
        self,
        template: str,
        dep_type: DependencyType | None = None,
    ) -> list[Dependency]:
        """Return direct dependencies of a template.

        :param template: The template name to query.
        :param dep_type: If given, filter to only this dependency type.
        """
        deps = self.edges.get(template, [])
        if dep_type is not None:
            return [d for d in deps if d.dep_type == dep_type]
        return list(deps)

    def get_dependents(
        self,
        template: str,
        dep_type: DependencyType | None = None,
    ) -> list[Dependency]:
        """Return templates that depend on the given template (reverse lookup).

        :param template: The template name to query.
        :param dep_type: If given, filter to only this dependency type.
        """
        deps = self.reverse_edges.get(template, [])
        if dep_type is not None:
            return [d for d in deps if d.dep_type == dep_type]
        return list(deps)

    def get_all_dependencies(
        self,
        template: str,
        dep_type: DependencyType | None = None,
    ) -> set[str]:
        """BFS transitive closure of all dependencies (excluding self).

        :param template: The starting template.
        :param dep_type: If given, follow only edges of this type.
        """
        visited: set[str] = set()
        queue: list[str] = [template]
        while queue:
            current = queue.pop(0)
            for dep in self.get_dependencies(current, dep_type):
                if dep.target is not None and dep.target not in visited:
                    visited.add(dep.target)
                    queue.append(dep.target)
        return visited

    def has_cycle(self, template: str) -> bool:
        """Check whether a template is involved in a dependency cycle."""
        if not self._cycle_nodes and self.nodes:
            self.detect_cycles()
        return template in self._cycle_nodes

    def detect_cycles(self) -> list[tuple[str, ...]]:
        """Detect all dependency cycles using DFS coloring.

        Results are cached after the first call.

        :returns: List of cycles, each a tuple of template names forming
            the cycle path (last element equals first).
        """
        if self.cycles:
            return self.cycles

        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self.nodes}
        path: list[str] = []
        found_cycles: list[tuple[str, ...]] = []

        def dfs(node: str) -> None:
            color[node] = GRAY
            path.append(node)
            for dep in self.edges.get(node, []):
                if dep.target is None:
                    continue
                if dep.target not in color:
                    # target not in graph nodes, skip
                    continue
                target_color = color[dep.target]
                if target_color == GRAY:
                    # back edge → cycle found
                    cycle_start = path.index(dep.target)
                    cycle = tuple(path[cycle_start:]) + (dep.target,)
                    found_cycles.append(cycle)
                    for n in cycle:
                        self._cycle_nodes.add(n)
                elif target_color == WHITE:
                    dfs(dep.target)
            path.pop()
            color[node] = BLACK

        for node in sorted(self.nodes):
            if color.get(node, WHITE) == WHITE:
                dfs(node)

        self.cycles = found_cycles
        return found_cycles

    def topological_sort(self) -> list[str] | None:
        """Return templates in topological order (dependencies first).

        :returns: Sorted list, or ``None`` if cycles exist.
        """
        if not self.cycles:
            self.detect_cycles()
        if self.cycles:
            return None

        # Kahn's algorithm: templates with no unresolved deps come first
        in_degree: dict[str, int] = {n: 0 for n in self.nodes}
        for source_name, deps in self.edges.items():
            for dep in deps:
                if dep.target is not None and dep.target in in_degree:
                    in_degree[source_name] += 1

        queue = sorted(n for n, d in in_degree.items() if d == 0)
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            # node is now "built"; reduce in-degree for templates that
            # depend on it
            for dep in self.reverse_edges.get(node, []):
                src = dep.source
                if src in in_degree:
                    in_degree[src] -= 1
                    if in_degree[src] == 0:
                        queue.append(src)
                        queue.sort()

        return result if len(result) == len(self.nodes) else None


def _extract_template_names(
    template_expr: nodes.Expr,
) -> t.Iterator[tuple[str | None, bool]]:
    """Extract template name(s) from a template expression node.

    Yields ``(name, is_dynamic)`` tuples.  For ``select_template``
    scenarios (Tuple/List), multiple names may be yielded.
    """
    if isinstance(template_expr, nodes.Const):
        if isinstance(template_expr.value, str):
            yield template_expr.value, False
        elif isinstance(template_expr.value, (tuple, list)):
            # Include with a tuple/list constant, e.g.
            # {% include ("a.html", "b.html") %}
            for item in template_expr.value:
                if isinstance(item, str):
                    yield item, False
                else:
                    yield None, True
        else:
            yield None, True
    elif isinstance(template_expr, (nodes.Tuple, nodes.List)):
        has_any = False
        for item in template_expr.items:
            if isinstance(item, nodes.Const) and isinstance(item.value, str):
                yield item.value, False
                has_any = True
            else:
                yield None, True
                has_any = True
        if not has_any:
            yield None, True
    else:
        yield None, True


def extract_dependencies(
    ast: nodes.Template,
    template_name: str,
) -> list[Dependency]:
    """Extract all direct dependencies from a template AST.

    This performs a single-pass scan of the AST and returns structured
    dependency information without executing the template.

    :param ast: The parsed template AST (from ``env.parse()``).
    :param template_name: The name of the template being analyzed.
    :returns: List of :class:`Dependency` instances.
    """
    dependencies: list[Dependency] = []

    for node in ast.find_all(_REF_TYPES):
        dep_type = _NODE_TYPE_MAP[type(node)]
        template_expr: nodes.Expr = node.template  # type: ignore[attr-defined]

        # Extract type-specific metadata
        with_context = False
        ignore_missing = False
        imported_names: tuple[t.Union[str, tuple[str, str]], ...] = ()

        if isinstance(node, (nodes.Include, nodes.Import, nodes.FromImport)):
            with_context = node.with_context
        if isinstance(node, nodes.Include):
            ignore_missing = node.ignore_missing
        if isinstance(node, nodes.FromImport):
            imported_names = tuple(
                n if isinstance(n, str) else (n[0], n[1])
                for n in node.names
            )

        # For Include with Tuple/List template expr (select_template),
        # generate one Dependency per target
        if isinstance(node, nodes.Include) and isinstance(
            template_expr, (nodes.Tuple, nodes.List)
        ):
            for name, is_dyn in _extract_template_names(template_expr):
                dependencies.append(
                    Dependency(
                        source=template_name,
                        target=name,
                        dep_type=dep_type,
                        lineno=node.lineno,
                        with_context=with_context,
                        ignore_missing=ignore_missing,
                        is_dynamic=is_dyn,
                    )
                )
            continue

        # For Include with Const tuple/list value
        if isinstance(node, nodes.Include) and isinstance(
            template_expr, nodes.Const
        ) and isinstance(template_expr.value, (tuple, list)):
            for name, is_dyn in _extract_template_names(template_expr):
                dependencies.append(
                    Dependency(
                        source=template_name,
                        target=name,
                        dep_type=dep_type,
                        lineno=node.lineno,
                        with_context=with_context,
                        ignore_missing=ignore_missing,
                        is_dynamic=is_dyn,
                    )
                )
            continue

        # Standard single-target case
        names = list(_extract_template_names(template_expr))
        if len(names) == 1:
            name, is_dynamic = names[0]
            dependencies.append(
                Dependency(
                    source=template_name,
                    target=name,
                    dep_type=dep_type,
                    lineno=node.lineno,
                    with_context=with_context,
                    ignore_missing=ignore_missing,
                    imported_names=imported_names,
                    is_dynamic=is_dynamic,
                )
            )
        else:
            # Shouldn't happen for Extends/Import/FromImport, but
            # handle gracefully
            for name, is_dyn in names:
                dependencies.append(
                    Dependency(
                        source=template_name,
                        target=name,
                        dep_type=dep_type,
                        lineno=node.lineno,
                        with_context=with_context,
                        ignore_missing=ignore_missing,
                        imported_names=imported_names,
                        is_dynamic=is_dyn,
                    )
                )

    return dependencies


def _dependency_to_meta_dict(dep: Dependency) -> dict[str, t.Any]:
    """Serialize a Dependency to a plain dict suitable for embedding
    in compiled Python modules."""
    return {
        "target": dep.target,
        "type": dep.dep_type.value,
        "lineno": dep.lineno,
        "with_context": dep.with_context,
        "ignore_missing": dep.ignore_missing,
        "imported_names": list(dep.imported_names),
        "is_dynamic": dep.is_dynamic,
    }


def _meta_dict_to_dependency(
    source: str,
    item: dict[str, t.Any],
) -> Dependency:
    """Deserialize a dependency metadata dict back into a Dependency."""
    imported_names = tuple(
        tuple(n) if isinstance(n, list) else n
        for n in item.get("imported_names", ())
    )
    return Dependency(
        source=source,
        target=item["target"],
        dep_type=DependencyType(item["type"]),
        lineno=item["lineno"],
        with_context=item.get("with_context", False),
        ignore_missing=item.get("ignore_missing", False),
        imported_names=imported_names,  # type: ignore[arg-type]
        is_dynamic=item.get("is_dynamic", False),
    )


def load_dependencies_from_module_dict(
    template_name: str,
    module_dict: dict[str, t.Any],
) -> list[Dependency]:
    """Read embedded dependency metadata from a precompiled module dict.

    :param template_name: The template name (used as the ``source``
        field on each dependency).
    :param module_dict: The ``__dict__`` of a precompiled template
        module.
    :returns: List of :class:`Dependency` instances, or empty list if
        no metadata is found.
    """
    raw_meta = module_dict.get("_jinja_dependency_meta")
    if raw_meta is None:
        return []
    return [_meta_dict_to_dependency(template_name, item) for item in raw_meta]


def build_dependency_graph(
    environment: "Environment",
    root_templates: t.Iterable[str] | None = None,
) -> DependencyGraph:
    """Build a complete dependency graph by BFS traversal.

    Starting from the given root templates (or all templates if
    ``root_templates`` is ``None``), this function parses each
    template's source and recursively follows dependency references.

    :param environment: A Jinja2 :class:`~jinja2.Environment` with a
        configured loader.
    :param root_templates: Starting template names. If ``None``, uses
        ``loader.list_templates()`` to scan all available templates.
    :returns: A :class:`DependencyGraph` with cycle detection applied.
    """
    graph = DependencyGraph()

    if root_templates is None:
        assert environment.loader is not None, (
            "No loader configured on the environment."
        )
        root_templates = environment.loader.list_templates()

    visited: set[str] = set()
    queue: list[str] = list(root_templates)

    while queue:
        name = queue.pop(0)
        if name in visited:
            continue
        visited.add(name)
        graph.nodes.add(name)

        try:
            source, filename, _ = environment.loader.get_source(
                environment, name
            )
            ast = environment.parse(source, name, filename)
        except Exception:
            # Template not found or parse error — record as an
            # isolated node
            continue

        deps = extract_dependencies(ast, name)
        for dep in deps:
            graph.add_dependency(dep)
            if dep.target is not None and dep.target not in visited:
                queue.append(dep.target)

    graph.detect_cycles()
    return graph


def build_dependency_graph_from_modules(
    module_dicts: dict[str, dict[str, t.Any]],
) -> DependencyGraph:
    """Build a dependency graph from precompiled module dictionaries.

    This is the precompiled-artifact counterpart to
    :func:`build_dependency_graph`. Each module dict is expected to
    contain a ``_jinja_dependency_meta`` variable embedded during
    compilation.

    :param module_dicts: Mapping of ``{template_name: module.__dict__}``.
    :returns: A :class:`DependencyGraph` with cycle detection applied.
    """
    graph = DependencyGraph()

    for template_name, mod_dict in module_dicts.items():
        graph.nodes.add(template_name)
        deps = load_dependencies_from_module_dict(template_name, mod_dict)
        for dep in deps:
            graph.add_dependency(dep)

    graph.detect_cycles()
    return graph
