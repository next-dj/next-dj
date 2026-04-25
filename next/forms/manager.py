"""`FormActionManager` aggregates backends and exposes URL patterns."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .backends import FormActionBackend, RegistryFormActionBackend


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from django import forms as django_forms
    from django.http import HttpRequest
    from django.urls import URLPattern

    from .backends import FormActionOptions


class FormActionManager:
    """Holds one or more backends and yields their URL patterns."""

    def __init__(
        self,
        backends: list[FormActionBackend] | None = None,
    ) -> None:
        """Initialize with given backends or default to `RegistryFormActionBackend`."""
        self._backends: list[FormActionBackend] = backends or [
            RegistryFormActionBackend(),
        ]

    def __repr__(self) -> str:
        """Return a debug representation showing the number of backends."""
        return f"<{self.__class__.__name__} backends={len(self._backends)}>"

    def __iter__(self) -> Iterator[URLPattern]:
        """Yield concatenated URL patterns from each backend."""
        for backend in self._backends:
            yield from backend.generate_urls()

    def register_action(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        options: FormActionOptions | None = None,
    ) -> None:
        """Forward registration to the first backend."""
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
        return self._backends[0]


form_action_manager = FormActionManager()


__all__ = ["FormActionManager", "form_action_manager"]
