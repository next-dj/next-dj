from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tests.support.trees import routed


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from django.core.checks import CheckMessage


def run_check(
    check: Callable[..., list[CheckMessage]], root: Path, **framework: object
) -> list[CheckMessage]:
    """Run one system check over the page tree `root` routed through a real router."""
    with routed(root, **framework):
        return check(None)


def located(messages: list[CheckMessage], root: Path) -> list[tuple[str, str]]:
    """Return each message as its id and the path of its object relative to `root`."""
    base = root.resolve()
    return [
        (message.id, str(Path(message.obj).resolve().relative_to(base)))
        for message in messages
    ]


def write_files(root: Path, files: Mapping[str, str]) -> None:
    """Write each `files` entry under `root`, creating the directories it needs."""
    for relative, source in files.items():
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(source)
