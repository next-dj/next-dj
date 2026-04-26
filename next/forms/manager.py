"""`FormActionManager` aggregates backends and exposes URL patterns.

Backends are loaded lazily from `NEXT_FRAMEWORK["DEFAULT_FORM_ACTION_BACKENDS"]`
on first access. Unlike the components and static managers, this manager
does **not** subscribe to `settings_reloaded`, because `@action` registers
handlers imperatively at import time. Auto-rebuilding on every reload
would drop those registrations and break test runs that rely on
session-scoped `eager_load_pages`. Tests that swap form-action settings
must call `next.testing.reset_form_actions()` explicitly to drop the
cached backend list.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from next.conf import next_framework_settings

from .backends import FormActionFactory


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from django import forms as django_forms
    from django.http import HttpRequest
    from django.urls import URLPattern

    from .backends import FormActionBackend, FormActionOptions


logger = logging.getLogger(__name__)


class FormActionManager:
    """Holds one or more backends and yields their URL patterns."""

    def __init__(
        self,
        backends: list[FormActionBackend] | None = None,
    ) -> None:
        """Initialise with explicit backends or defer loading to settings."""
        self._backends: list[FormActionBackend] = list(backends) if backends else []

    def __repr__(self) -> str:
        """Return a debug representation showing the number of backends."""
        return f"<{self.__class__.__name__} backends={len(self._backends)}>"

    def __iter__(self) -> Iterator[URLPattern]:
        """Yield concatenated URL patterns from each backend."""
        self._ensure_backends()
        for backend in self._backends:
            yield from backend.generate_urls()

    def _reload_config(self) -> None:
        self._backends = []
        configs = cast(
            "list[Any]",
            getattr(next_framework_settings, "DEFAULT_FORM_ACTION_BACKENDS", []),
        )
        for config in configs:
            if not isinstance(config, dict):
                continue
            try:
                self._backends.append(FormActionFactory.create_backend(config))
            except Exception:
                logger.exception(
                    "Error creating form-action backend from config %s",
                    config,
                )

    def _ensure_backends(self) -> None:
        if not self._backends:
            self._reload_config()

    def register_action(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        options: FormActionOptions | None = None,
    ) -> None:
        """Forward registration to the first backend."""
        self._ensure_backends()
        self._backends[0].register_action(name, handler, options=options)

    def clear_registries(self) -> None:
        """Clear every backend that exposes a `clear_registry` method.

        Intended for test isolation. Backends that do not implement
        `clear_registry` are skipped silently.
        """
        for backend in self._backends:
            clear = getattr(backend, "clear_registry", None)
            if callable(clear):
                clear()

    def get_action_url(self, action_name: str) -> str:
        """Return the reverse URL from the first backend that knows `action_name`."""
        self._ensure_backends()
        for backend in self._backends:
            if backend.get_meta(action_name) is not None:
                return backend.get_action_url(action_name)
        msg = f"Unknown form action: {action_name}"
        raise KeyError(msg)

    def render_form_fragment(
        self,
        request: HttpRequest,
        action_name: str,
        form: django_forms.Form | None,
        template_fragment: str | None = None,
        *,
        page_file_path: Path | None = None,
    ) -> str:
        """Delegate rendering to the first backend."""
        self._ensure_backends()
        return self._backends[0].render_form_fragment(
            request,
            action_name,
            form,
            template_fragment,
            page_file_path=page_file_path,
        )

    @property
    def default_backend(self) -> FormActionBackend:
        """Return the first configured backend."""
        self._ensure_backends()
        return self._backends[0]


form_action_manager = FormActionManager()


__all__ = ["FormActionManager", "form_action_manager"]
