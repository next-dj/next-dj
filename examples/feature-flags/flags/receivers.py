from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from next.components.signals import component_rendered
from next.forms.signals import form_access_denied
from next.pages.signals import page_rendered

from .cache import invalidate_flag
from .metrics import record_render
from .models import Flag


if TYPE_CHECKING:
    from pathlib import Path


PAGES_DIR_NAME = "panels"
GUARD_COUNT_KEY = "flags:feature_guard:count"
DENIED_COUNT_KEY = "flags:access_denied:count"
_guard_lock = threading.Lock()
_denied_lock = threading.Lock()


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


def _bump(key: str, lock: threading.Lock) -> None:
    """Atomically increment a process-lifetime counter in the cache."""
    with lock:
        cache.add(key, 0)
        cache.incr(key)


def _read(key: str) -> int:
    """Return the current value of a counter, treating a miss as zero."""
    return int(cache.get(key) or 0)


@receiver(component_rendered)
def _count_feature_guard(sender: object, info: object, **_: object) -> None:  # noqa: ARG001
    """Bump a counter every time the `feature_guard` component is rendered."""
    if getattr(info, "name", None) != "feature_guard":
        return
    _bump(GUARD_COUNT_KEY, _guard_lock)


@receiver(form_access_denied)
def _count_access_denied(**_: object) -> None:
    """Bump a counter whenever a form permission hook denies a request."""
    _bump(DENIED_COUNT_KEY, _denied_lock)


def feature_guard_count() -> int:
    """Return the number of `feature_guard` component renders this process."""
    return _read(GUARD_COUNT_KEY)


def access_denied_count() -> int:
    """Return the number of permission-hook denials this process."""
    return _read(DENIED_COUNT_KEY)
