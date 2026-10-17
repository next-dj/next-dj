"""The applications of a project and the edges that make one depend on another."""

from collections import defaultdict, deque
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from django.apps import apps as django_apps


type EdgeKind = Literal[
    "import", "fk", "get_model", "signal", "di", "zone", "component", "perm"
]

_INSTALLED_DIRS: Final = frozenset({"site-packages", "dist-packages"})
_FRAMEWORK_PACKAGES: Final = ("next", "django")
_TANGLED: Final = 2


@dataclass(frozen=True, slots=True)
class App:
    """A project application, a node of the graph named by its label."""

    label: str
    name: str
    path: Path

    def owns_module(self, module: str) -> bool:
        """Return whether the dotted `module` lies in this application."""
        return module == self.name or module.startswith(f"{self.name}.")

    def submodule(self, module: str) -> str:
        """Return the part of `module` below the application package."""
        return module.removeprefix(self.name).removeprefix(".")


@dataclass(frozen=True, slots=True)
class Edge:
    """One reason the application `source` depends on the application `target`."""

    kind: EdgeKind
    source: str
    target: str
    file: Path
    line: int
    detail: str


def edge_order(edge: Edge) -> tuple[str, int, str, str, str]:
    """Return the key edges sort by, so every report lists them the same way."""
    return (str(edge.file), edge.line, edge.kind, edge.target, edge.detail)


@dataclass(frozen=True, slots=True)
class Graph:
    """The project applications and every edge found between two of them."""

    apps: tuple[App, ...]
    edges: tuple[Edge, ...]

    def app(self, label: str) -> App | None:
        """Return the application of `label`, `None` for no project application."""
        return next((app for app in self.apps if app.label == label), None)

    def outgoing(self, label: str) -> tuple[Edge, ...]:
        """Return the edges from the application `label` to another one."""
        return tuple(edge for edge in self.edges if edge.source == label)

    def incoming(self, label: str) -> tuple[Edge, ...]:
        """Return the edges from another application to the application `label`."""
        return tuple(edge for edge in self.edges if edge.target == label)

    def tangles(self) -> tuple["Tangle", ...]:
        """Return each group of two or more apps that reach one another, by label.

        The strongly connected components are found in linear time, by Tarjan.
        """
        first: dict[tuple[str, str], Edge] = {}
        for edge in sorted(self.edges, key=edge_order):
            if edge.source != edge.target:
                first.setdefault((edge.source, edge.target), edge)
        successors: dict[str, list[str]] = defaultdict(list)
        for source, target in sorted(first):
            successors[source].append(target)
        found: list[Tangle] = []
        for component in _components(successors):
            if len(component) < _TANGLED:
                continue
            labels = tuple(sorted(component))
            route = _witness(labels[0], successors, component)
            cycle = tuple(
                first[route[index], route[(index + 1) % len(route)]]
                for index in range(len(route))
            )
            pairs = tuple(
                sorted(
                    (
                        edge
                        for (source, target), edge in first.items()
                        if source in component and target in component
                    ),
                    key=edge_order,
                )
            )
            found.append(Tangle(labels, cycle, pairs))
        return tuple(sorted(found, key=lambda tangle: tangle.labels))


@dataclass(frozen=True, slots=True)
class Tangle:
    """Apps that all depend on one another, with the shortest cycle through the first.

    `cycle` and `pairs` hold the first edge in order of each step and of each pair.
    """

    labels: tuple[str, ...]
    cycle: tuple[Edge, ...]
    pairs: tuple[Edge, ...]


def _components(successors: Mapping[str, list[str]]) -> list[frozenset[str]]:
    """Return the strongly connected components of the graph, by Tarjan, iteratively."""
    nodes = sorted({*successors, *(t for ts in successors.values() for t in ts)})
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[frozenset[str]] = []
    for root in nodes:
        if root in index:
            continue
        work: list[tuple[str, Iterator[str]]] = [(root, iter(successors.get(root, ())))]
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        while work:
            node, pending = work[-1]
            following = next(pending, None)
            if following is None:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    components.append(_pop_component(node, stack, on_stack))
            elif following not in index:
                index[following] = low[following] = len(index)
                stack.append(following)
                on_stack.add(following)
                work.append((following, iter(successors.get(following, ()))))
            elif following in on_stack:
                low[node] = min(low[node], index[following])
    return components


def _pop_component(node: str, stack: list[str], on_stack: set[str]) -> frozenset[str]:
    """Pop the stack down to `node`, returning the component it closes."""
    members: set[str] = set()
    while True:
        member = stack.pop()
        on_stack.discard(member)
        members.add(member)
        if member == node:
            return frozenset(members)


def _witness(
    start: str, successors: Mapping[str, list[str]], component: frozenset[str]
) -> list[str]:
    """Return the shortest cycle through `start` inside its component, by BFS.

    The component holds a cycle through every member, so the search always ends.
    """
    parents: dict[str, str] = {}
    queue = deque([start])
    while True:
        node = queue.popleft()
        for following in successors.get(node, ()):
            if following == start:
                route = [node]
                while route[-1] != start:
                    route.append(parents[route[-1]])
                return route[::-1]
            if following in component and following not in parents:
                parents[following] = node
                queue.append(following)


def _framework(name: str) -> bool:
    """Return whether `name` is a module of next.dj or Django, never a node."""
    return any(
        name == package or name.startswith(f"{package}.")
        for package in _FRAMEWORK_PACKAGES
    )


def project_apps(base_dir: Path | None) -> tuple[App, ...]:
    """Return the installed applications that live under `base_dir`, by label.

    An application installed as a distribution is a dependency, never a node.
    """
    if base_dir is None:
        return ()
    root = base_dir.resolve()
    found: list[App] = []
    for config in django_apps.get_app_configs():
        path = Path(config.path).resolve()
        if _framework(config.name) or not path.is_relative_to(root):
            continue
        if _INSTALLED_DIRS.intersection(path.parts):
            continue
        found.append(App(config.label, config.name, path))
    return tuple(sorted(found, key=lambda app: app.label))


def path_owner(apps: Iterable[App], path: Path) -> App | None:
    """Return the innermost application whose directory holds `path`."""
    owners = [app for app in apps if path.is_relative_to(app.path)]
    return max(owners, key=lambda app: len(app.path.parts), default=None)


def module_owner(apps: Iterable[App], module: str) -> App | None:
    """Return the innermost application whose package holds the dotted `module`."""
    owners = [app for app in apps if app.owns_module(module)]
    return max(owners, key=lambda app: len(app.name), default=None)


__all__ = [
    "App",
    "Edge",
    "EdgeKind",
    "Graph",
    "Tangle",
    "edge_order",
    "module_owner",
    "path_owner",
    "project_apps",
]
