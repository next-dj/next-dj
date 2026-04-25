"""Context-processor discovery and loading.

Context processors come from two sources. First, each entry in
`NEXT_FRAMEWORK["DEFAULT_PAGE_BACKENDS"]` may list processors under
`OPTIONS.context_processors`. Second, Django's `TEMPLATES` setting
includes its own `OPTIONS.context_processors`. Both sources merge with
Next-router entries taking precedence and duplicates dropped.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.utils.module_loading import import_string

from next.conf import next_framework_settings


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


def _get_context_processors() -> list[Callable[[Any], dict[str, Any]]]:
    """Return the merged context processors from Next routers and Django."""
    configs = next_framework_settings.DEFAULT_PAGE_BACKENDS
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
