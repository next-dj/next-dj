from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

from next.conf import USER_SETTING
from tests.support.trees import routed


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

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


def write_file(path: Path, text: str) -> Path:
    """Write `text` to `path`, creating the directories it needs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def write_files(root: Path, files: Mapping[str, str]) -> None:
    """Write each `files` entry under `root`, creating the directories it needs."""
    for relative, source in files.items():
        write_file(root / relative, source)


@contextmanager
def written(framework: object) -> Iterator[None]:
    """Write `NEXT_FRAMEWORK` as a check reads it, with no reload to refuse a misfit.

    `patch.object(..., create=True)` deletes an attribute `LazySettings` has not cached.
    """
    missing = object()
    before = getattr(settings, USER_SETTING, missing)
    setattr(settings, USER_SETTING, framework)
    try:
        yield
    finally:
        if before is missing:
            delattr(settings, USER_SETTING)
        else:
            setattr(settings, USER_SETTING, before)
