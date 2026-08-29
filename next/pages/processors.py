"""Context-processor discovery and loading.

Context processors come from two sources. First, each entry in
`NEXT_FRAMEWORK["PAGE_BACKENDS"]` may list processors under
`OPTIONS.context_processors`. Second, Django's `TEMPLATES` setting
includes its own `OPTIONS.context_processors`. Both sources merge with
Next-router entries taking precedence and duplicates dropped.
"""

from __future__ import annotations

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


# A single-slot holder mutated in place, so a reset needs no `global`.
_CONTEXT_PROCESSORS_CACHE: dict[str, list[Callable[[Any], dict[str, Any]]] | None] = {
    "value": None
}


def _get_context_processors() -> list[Callable[[Any], dict[str, Any]]]:
    """Return the merged context processors from Next routers and Django.

    The merge and its imports depend on settings alone, so the result is
    memoised until either source setting changes.
    """
    cached = _CONTEXT_PROCESSORS_CACHE["value"]
    if cached is None:
        cached = _build_context_processors()
        _CONTEXT_PROCESSORS_CACHE["value"] = cached
    return cached


def _build_context_processors() -> list[Callable[[Any], dict[str, Any]]]:
    """Merge the router and `TEMPLATES` processor paths and import each one."""
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
    _CONTEXT_PROCESSORS_CACHE["value"] = None


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Drop the memo when Django reports a `TEMPLATES` change.

    `settings_reloaded` covers only the `NEXT_FRAMEWORK` half of the merge.
    """
    if setting == "TEMPLATES":
        _reset_context_processors_cache()


settings_reloaded.connect(_reset_context_processors_cache)
setting_changed.connect(_on_setting_changed)
