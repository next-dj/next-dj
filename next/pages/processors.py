"""Context-processor discovery and loading.

Sourced from `PAGE_BACKENDS` and Django's `TEMPLATES`, Next-router entries winning ties.
"""

from __future__ import annotations

import functools
import logging
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.signals import setting_changed
from django.utils.module_loading import import_string

from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded


if TYPE_CHECKING:
    from collections.abc import Callable


logger = logging.getLogger(__name__)


def _import_context_processor(
    processor_path: str,
) -> Callable[[Any], dict[str, Any]] | None:
    """Import a context processor callable or return None on failure."""
    try:
        processor = import_string(processor_path)
        if callable(processor):
            return processor  # type: ignore[no-any-return]
    except (ImportError, AttributeError) as e:
        logger.warning("Could not import context processor %s: %s", processor_path, e)
    return None


@functools.cache
def _get_context_processors() -> list[Callable[[Any], dict[str, Any]]]:
    """Merge the router and `TEMPLATES` processor paths and import each one.

    The merge depends on settings alone, so it is memoised until one of them changes.
    """
    configs = next_framework_settings.PAGE_BACKENDS
    if not isinstance(configs, list):
        configs = []
    from_next = [
        path
        for c in configs
        if isinstance(c, dict)
        for path in (c.get("OPTIONS", {}).get("context_processors") or [])
        if isinstance(path, str)
    ]
    templates = getattr(settings, "TEMPLATES", [])
    opts = templates[0].get("OPTIONS", {}) if templates else {}
    from_templates = (
        list(opts.get("context_processors", []))
        if isinstance(opts.get("context_processors"), list)
        else []
    )
    processor_paths = list(dict.fromkeys(from_next + from_templates))
    return [p for path in processor_paths if (p := _import_context_processor(path))]


def _reset_context_processors_cache(**kwargs) -> None:
    """Drop the memoised processors so the next render rebuilds the list."""
    _get_context_processors.cache_clear()


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Drop the memo when Django reports a `TEMPLATES` change.

    `settings_reloaded` covers only the `NEXT_FRAMEWORK` half of the merge.
    """
    if setting == "TEMPLATES":
        _reset_context_processors_cache()


settings_reloaded.connect(_reset_context_processors_cache)
setting_changed.connect(_on_setting_changed)
