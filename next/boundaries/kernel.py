"""The shared kernel of a project, the app whose name ends in `core`."""

from dataclasses import dataclass
from typing import Final

from .graph import Graph
from .sources import Project


_KERNEL: Final = "core"


@dataclass(frozen=True, slots=True)
class Kernel:
    """How large a shared kernel is and how many other apps lean on it."""

    label: str
    modules: int
    lines: int
    users: int


def is_kernel(name: str) -> bool:
    """Return whether the dotted app `name` names a shared kernel."""
    return name.rpartition(".")[2] == _KERNEL


def kernels(project: Project | None, graph: Graph) -> tuple[Kernel, ...]:
    """Return the size and the users of each shared kernel of the project, by label.

    A line counts when it holds more than whitespace, and tests and migrations do not.
    """
    if project is None:
        return ()
    found: list[Kernel] = []
    for app in graph.apps:
        if not is_kernel(app.name):
            continue
        files = project.files_of(app.label)
        lines = sum(
            1
            for source in files
            for line in source.path.read_bytes().splitlines()
            if line.strip()
        )
        users = {edge.source for edge in graph.incoming(app.label)}
        found.append(Kernel(app.label, len(files), lines, len(users)))
    return tuple(found)


__all__ = ["Kernel", "is_kernel", "kernels"]
