from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from next.components.signals import component_rendered
from next.pages.signals import page_rendered

from .cache import invalidate_flag
from .metrics import record_render
from .models import Flag


if TYPE_CHECKING:
    from pathlib import Path


PAGES_DIR_NAME = "panels"
GUARD_COUNT_KEY = "flags:feature_guard:count"
_guard_lock = threading.Lock()


def _page_key(file_path: Path) -> str:
    """Return a stable per-page key derived from the path under `panels/`."""
    parts = file_path.parts
    try:
        anchor = parts.index(PAGES_DIR_NAME)
    except ValueError:
        return file_path.stem
    segments = parts[anchor + 1 : -1]
    return "/".join(segments) if segments else "/"


@receiver(post_save, sender=Flag)
def _invalidate_on_save(sender: type[Flag], instance: Flag, **_: object) -> None:  # noqa: ARG001
    """Drop the cached entry so the next read reflects the updated row."""
    invalidate_flag(instance.name)


@receiver(post_delete, sender=Flag)
def _invalidate_on_delete(sender: type[Flag], instance: Flag, **_: object) -> None:  # noqa: ARG001
    """Drop the cached entry when the flag is removed from the database."""
    invalidate_flag(instance.name)


@receiver(page_rendered)
def _count_page_render(sender: object, file_path: Path, **_: object) -> None:  # noqa: ARG001
    """Count how many times each page rendered while the process is alive."""
    record_render(_page_key(file_path))


@receiver(component_rendered)
def _count_feature_guard(sender: object, info: object, **_: object) -> None:  # noqa: ARG001
    """Bump a counter every time the `feature_guard` component is rendered."""
    if getattr(info, "name", None) != "feature_guard":
        return
    with _guard_lock:
        cache.add(GUARD_COUNT_KEY, 0)
        cache.incr(GUARD_COUNT_KEY)


def feature_guard_count() -> int:
    """Return the number of `feature_guard` component renders this process."""
    return int(cache.get(GUARD_COUNT_KEY) or 0)
