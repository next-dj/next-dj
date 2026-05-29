"""Manager for form action backends and routing."""

import logging
import types
from typing import TYPE_CHECKING, Any, cast

from django.core.exceptions import ImproperlyConfigured

from next.conf import next_framework_settings

from ._request_utils import _url_kwargs_from_resolver_or_post
from .backends import FormActionFactory
from .dispatch import _form_action_context_callable


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from django import forms as django_forms
    from django.http import HttpRequest
    from django.urls import URLPattern

    from .backends import FormActionBackend


logger = logging.getLogger(__name__)


class FormActionManager:
    """Holds one or more backends and yields their URL patterns."""

    def __init__(
        self,
        backends: "list[FormActionBackend] | None" = None,
    ) -> None:
        """Initialise with explicit backends or defer loading to settings."""
        self._backends: list[FormActionBackend] = list(backends) if backends else []

    def __repr__(self) -> str:
        """Return a debug representation showing the number of backends."""
        return f"<{self.__class__.__name__} backends={len(self._backends)}>"

    def __iter__(self) -> "Iterator[URLPattern]":
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
            except ImproperlyConfigured:
                logger.exception(
                    "Error creating form-action backend from config %s",
                    config,
                )

    def _ensure_backends(self) -> None:
        if not self._backends:
            self._reload_config()

    def register_action(  # noqa: PLR0913
        self,
        name: str,
        *,
        form_class: "type[django_forms.Form] | Callable[..., Any] | None" = None,
        handler: "Callable[..., Any] | None" = None,
        wizard_class: "type | None" = None,
        file_path: str,
        scope: str,
    ) -> None:
        """Forward registration to the first backend."""
        self._ensure_backends()
        self._backends[0].register_action(
            name,
            form_class=form_class,
            handler=handler,
            wizard_class=wizard_class,
            file_path=file_path,
            scope=scope,
        )

    def clear_registries(self) -> None:
        """Clear every backend that exposes a `clear_registry` method.

        Intended for test isolation. Backends that do not implement
        `clear_registry` are skipped silently.
        """
        for backend in self._backends:
            clear = getattr(backend, "clear_registry", None)
            if callable(clear):
                clear()

    def get_action_url(self, action_name: str, *, page_path: str | None = None) -> str:
        """Return the reverse URL from the first backend that knows `action_name`."""
        self._ensure_backends()
        for backend in self._backends:
            if backend.get_meta(action_name, page_path=page_path) is not None:
                return backend.get_action_url(action_name, page_path=page_path)
        msg = f"Unknown form action: {action_name}"
        raise KeyError(msg)

    def render_form_fragment(
        self,
        request: "HttpRequest",
        action_name: str,
        form: "django_forms.Form | None",
        template_fragment: str | None = None,
        *,
        page_file_path: "Path | None" = None,
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
    def default_backend(self) -> "FormActionBackend":
        """Return the first configured backend."""
        self._ensure_backends()
        return self._backends[0]


form_action_manager = FormActionManager()


def build_form_namespace_for_action(
    action_name: str,
    request: "HttpRequest",
    page_path: str | None = None,
) -> types.SimpleNamespace | None:
    """Build the form namespace used by the form template tag."""
    form_action_manager._ensure_backends()
    for backend in form_action_manager._backends:
        meta = backend.get_meta(action_name, page_path=page_path)
        if meta is None:
            continue
        wizard_class = meta.get("wizard_class")
        if wizard_class is not None:
            url_kwargs = _url_kwargs_from_resolver_or_post(request)
            wizard = wizard_class(request=request, url_kwargs=url_kwargs)
            return cast("types.SimpleNamespace", wizard.template_namespace())
        fc = meta.get("form_class")
        if fc is None:
            return None
        return _form_action_context_callable(fc)(request)
    return None


__all__ = [
    "FormActionManager",
    "build_form_namespace_for_action",
    "form_action_manager",
]
