"""Run the async parts of one inline component render from a sync template render."""

from __future__ import annotations

import logging
import threading
from time import perf_counter
from typing import TYPE_CHECKING

from django.conf import settings

from next.ports import run_bridged


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from .info import ComponentInfo


logger = logging.getLogger("next.components")

_reported: set[Path | None] = set()
_reported_lock = threading.Lock()


def bridge[T](info: ComponentInfo, work: Callable[[], Awaitable[T]]) -> T:
    """Run `work` through the bridge port, logging its cost once per component in DEBUG.

    The cost leaves out the time `work` itself took, so it names the bridge alone.
    """
    site = info.module_path
    if not settings.DEBUG or site in _reported:
        return run_bridged(work)
    marks: list[float] = []

    async def measured() -> T:
        marks.append(perf_counter())
        value = await work()
        marks.append(perf_counter())
        return value

    started = perf_counter()
    value = run_bridged(measured)
    cost = perf_counter() - started - (marks[1] - marks[0])
    with _reported_lock:
        if site in _reported:
            return value
        _reported.add(site)
    logger.debug(
        "Component %r (%s) is rendered through a sync-to-async bridge on every "
        "render, as it has async code and renders inline in a sync template. "
        "The bridge cost %.3f ms beyond the component's own work.",
        info.name,
        site,
        cost * 1000,
    )
    return value


__all__ = ["bridge"]
