"""Manager for form action backends and routing."""

import logging
import types
from typing import TYPE_CHECKING, Any, cast

from django.core.exceptions import ImproperlyConfigured

from next.conf import next_framework_settings

from ._request_utils import _url_kwargs_for_request
from .backends import FormActionFactory
from .dispatch import _form_action_context_callable
from .exceptions import FormActionNotFound


if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.http import HttpRequest
    from django.urls import URLPattern

    from .backends import ActionMeta, ActionRegistration, FormActionBackend


logger = logging.getLogger(__name__)


def _action_url_or_none(
    backend: "FormActionBackend",
    action_name: str,
    page_path: str | None,
) -> str | None:
    """Return the backend URL for the action, or `None` when it is unknown."""
    try:
        return backend.get_action_url(action_name, page_path=page_path)
    except FormActionNotFound:
        return None


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
            getattr(next_framework_settings, "FORM_ACTION_BACKENDS", []),
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

    def _first_backend(self) -> "FormActionBackend":
        """Return the first backend or raise when none are configured."""
        self._ensure_backends()
        if not self._backends:
            msg = (
                "No form action backends configured. Add at least one entry to "
                "NEXT_FRAMEWORK['FORM_ACTION_BACKENDS']."
            )
            raise ImproperlyConfigured(msg)
        return self._backends[0]

    def register_action(self, registration: "ActionRegistration") -> None:
        """Forward registration to the first backend."""
        self._first_backend().register_action(registration)

    def clear_registries(self) -> None:
        """Clear every backend exposing `clear_registry`. For test isolation."""
        for backend in self._backends:
            clear = getattr(backend, "clear_registry", None)
            if callable(clear):
                clear()

    def get_action_url(self, action_name: str, *, page_path: str | None = None) -> str:
        """Return the reverse URL from the first backend that knows `action_name`."""
        self._ensure_backends()
        for backend in self._backends:
            url = _action_url_or_none(backend, action_name, page_path)
            if url is not None:
                return url
        msg = (
            f"Unknown form action {action_name!r}. Searched page scope for "
            f"{page_path or 'no page'} and the shared registry."
        )
        raise FormActionNotFound(msg, name=action_name, page_path=page_path)

    def get_action_meta(
        self,
        action_name: str,
        *,
        page_path: str | None = None,
    ) -> "ActionMeta | None":
        """Return the action meta from the first backend that knows the name."""
        self._ensure_backends()
        for backend in self._backends:
            meta = backend.get_meta(action_name, page_path)
            if meta is not None:
                return meta
        return None

    @property
    def default_backend(self) -> "FormActionBackend":
        """Return the first configured backend."""
        return self._first_backend()


form_action_manager = FormActionManager()


def build_form_namespace_for_action(
    action_name: str,
    request: "HttpRequest",
    page_path: str | None = None,
) -> types.SimpleNamespace | None:
    """Build the form namespace used by the form template tag."""
    meta = form_action_manager.get_action_meta(action_name, page_path=page_path)
    if meta is None:
        return None
    return _build_form_namespace_from_meta(meta, request)


def _build_form_namespace_from_meta(
    meta: "ActionMeta",
    request: "HttpRequest",
) -> types.SimpleNamespace | None:
    """Build the form namespace for already-resolved action meta."""
    wizard_class = meta.get("wizard_class")
    if wizard_class is not None:
        url_kwargs = _url_kwargs_for_request(request)
        wizard = wizard_class(request=request, url_kwargs=url_kwargs)
        return cast("types.SimpleNamespace", wizard.template_namespace())
    fc = meta.get("form_class")
    if fc is None:
        return None
    return _form_action_context_callable(fc)(request)


__all__ = [
    "FormActionManager",
    "build_form_namespace_for_action",
    "form_action_manager",
]
