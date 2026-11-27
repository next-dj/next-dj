"""Context-processor discovery and loading, and the context a page renders in.

Processors come from `PAGE_BACKENDS`, then from the engine every page compiles on.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.template import Context, Engine

from next.caches import SETTINGS, memo
from next.conf import next_framework_settings
from next.conf.imports import import_callable


if TYPE_CHECKING:
    from collections.abc import Callable


logger = logging.getLogger(__name__)


def _import_context_processor(
    processor_path: str,
) -> Callable[[Any], dict[str, Any]] | None:
    """Import a context processor callable, warning and returning `None` on failure."""
    processor = import_callable(processor_path)
    if processor is None:
        logger.warning("Could not import context processor %s", processor_path)
    return cast("Callable[[Any], dict[str, Any]] | None", processor)


def _engine_processors() -> list[str]:
    """Return the processor paths of the engine a page compiles on, none without one.

    `Template` compiles on `Engine.get_default()`, the first `DjangoTemplates` entry.
    """
    try:
        engine = Engine.get_default()
    except ImproperlyConfigured:
        return []
    paths: object = engine.context_processors
    if not isinstance(paths, list | tuple):
        return []
    return [path for path in paths if isinstance(path, str)]


@memo(SETTINGS)
def _get_context_processors() -> list[Callable[[Any], dict[str, Any]]]:
    """Merge the router and `TEMPLATES` processor paths and import each one.

    The merge depends on settings alone, so it is memoised until one of them changes.
    """
    configs = next_framework_settings.PAGE_BACKENDS
    from_next = [
        path
        for c in configs
        if isinstance(c, dict)
        for path in (c.get("OPTIONS", {}).get("context_processors") or [])
        if isinstance(path, str)
    ]
    from_templates = _engine_processors()
    processor_paths = list(dict.fromkeys(from_next + from_templates))
    return [p for path in processor_paths if (p := _import_context_processor(path))]


def context_processors() -> list[Callable[[Any], dict[str, Any]]]:
    """Return the context processors a page render runs, in the order it runs them."""
    return _get_context_processors()


class PageContext(Context):
    """A template context that carries its request, as a `RequestContext` does.

    The page ran its processors already, so binding a template runs none again.
    """

    def __init__(
        self,
        dict_: dict[str, object],
        request: HttpRequest,
        *,
        autoescape: bool = True,
        use_l10n: bool | None = None,
        use_tz: bool | None = None,
    ) -> None:
        """Hold `dict_` and the request a tag such as `{% url %}` reads its app from."""
        super().__init__(dict_, autoescape=autoescape, use_l10n=use_l10n, use_tz=use_tz)
        self.request = request


def page_context(
    data: dict[str, object],
    request: object,
    *,
    autoescape: bool = True,
    use_l10n: bool | None = None,
    use_tz: bool | None = None,
) -> Context:
    """Return the context a page or a boundary renders in, carrying its request."""
    if isinstance(request, HttpRequest):
        return PageContext(
            data, request, autoescape=autoescape, use_l10n=use_l10n, use_tz=use_tz
        )
    return Context(data, autoescape=autoescape, use_l10n=use_l10n, use_tz=use_tz)
