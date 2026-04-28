"""Form action backend contract and registry-based default implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict, cast

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponseNotFound
from django.urls import path, reverse
from django.urls.exceptions import NoReverseMatch
from django.views.decorators.http import require_http_methods

from next.conf import import_class_cached

from .checks import record_possible_collision
from .dispatch import FormActionDispatch
from .rendering import render_form_page_with_errors
from .signals import action_registered
from .uid import (
    FORM_ACTION_REVERSE_NAME,
    URL_NAME_FORM_ACTION,
    _make_uid,
    validated_next_form_page_path,
)


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from django import forms as django_forms
    from django.http import HttpRequest, HttpResponse
    from django.urls import URLPattern


class ActionMeta(TypedDict, total=False):
    """Per-action data stored in the registry backend."""

    handler: Callable[..., Any]
    form_class: type[django_forms.Form] | None
    uid: str


@dataclass
class FormActionOptions:
    """Options passed to `register_action` (used by the `@action` decorator)."""

    form_class: type[django_forms.Form] | None = None
    namespace: str | None = None


class FormActionBackend(ABC):
    """Storage and HTTP dispatch for `@action` handlers."""

    @abstractmethod
    def register_action(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        options: FormActionOptions | None = None,
    ) -> None:
        """Record an action from the decorator."""

    @abstractmethod
    def get_action_url(self, action_name: str) -> str:
        """Return the reverse URL for `action_name`."""

    @abstractmethod
    def generate_urls(self) -> list[URLPattern]:
        """Return URLconf entries for this backend."""

    @abstractmethod
    def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
        """Run the handler for `uid`."""

    def get_meta(self, action_name: str) -> dict[str, Any] | None:  # noqa: ARG002
        """Return optional per-action metadata for subclasses."""
        return None

    def render_form_fragment(
        self,
        request: HttpRequest,  # noqa: ARG002
        action_name: str,  # noqa: ARG002
        form: django_forms.Form | None,  # noqa: ARG002
        template_fragment: str | None = None,  # noqa: ARG002
        *,
        page_file_path: Path | None = None,  # noqa: ARG002
    ) -> str:
        """Return custom HTML for validation errors (override in subclasses)."""
        return ""


class RegistryFormActionBackend(FormActionBackend):
    """In-memory actions behind one dispatcher path keyed by UID."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:  # noqa: ARG002
        """Create an empty action map. `config` is accepted for factory parity."""
        self._registry: dict[str, ActionMeta] = {}
        self._uid_to_name: dict[str, str] = {}

    def clear_registry(self) -> None:
        """Drop every registered action and reset the UID index.

        Intended for test isolation. Use this to clear actions between
        independent test sessions that register overlapping names.
        """
        self._registry.clear()
        self._uid_to_name.clear()

    def register_action(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        options: FormActionOptions | None = None,
    ) -> None:
        """Store handler, optional form class, and stable uid for the action name."""
        opts = options or FormActionOptions()
        uid = _make_uid(name)
        existing = self._uid_to_name.get(uid)
        if existing is not None and existing != name:
            msg = (
                f"Form action UID collision: {existing!r} and {name!r} both "
                f"hash to {uid!r}. Rename one of them."
            )
            raise ImproperlyConfigured(msg)
        self._uid_to_name[uid] = name
        previous = self._registry.get(name)
        if previous is not None:
            record_possible_collision(name, previous["handler"], handler)
        self._registry[name] = {
            "handler": handler,
            "form_class": opts.form_class,
            "uid": uid,
        }
        action_registered.send(
            sender=self.__class__,
            action_name=name,
            uid=uid,
            form_class=opts.form_class,
            namespace=opts.namespace,
            handler=handler,
        )

    def get_action_url(self, action_name: str) -> str:
        """Return the reverse URL for a registered action name."""
        if action_name not in self._registry:
            msg = f"Unknown form action: {action_name}"
            raise KeyError(msg)
        uid = self._registry[action_name]["uid"]
        kwargs = {"uid": uid}
        try:
            return reverse(FORM_ACTION_REVERSE_NAME, kwargs=kwargs)
        except NoReverseMatch:
            return reverse(URL_NAME_FORM_ACTION, kwargs=kwargs)

    def generate_urls(self) -> list[URLPattern]:
        """Return one catch-all route when at least one action is registered."""
        if not self._registry:
            return []
        view = require_http_methods(["GET", "POST"])(self.dispatch)
        return [path("_next/form/<str:uid>/", view, name=URL_NAME_FORM_ACTION)]

    def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
        """Forward a POST request to `FormActionDispatch.dispatch`."""
        action_name = self._uid_to_name.get(uid)
        if action_name not in self._registry:
            return HttpResponseNotFound()
        meta = self._registry[action_name]
        return FormActionDispatch.dispatch(
            self, request, action_name, cast("dict[str, Any]", meta)
        )

    def get_meta(self, action_name: str) -> dict[str, Any] | None:
        """Return stored `ActionMeta` for the name, if any."""
        return cast("dict[str, Any] | None", self._registry.get(action_name))

    def render_form_fragment(
        self,
        request: HttpRequest,
        action_name: str,
        form: django_forms.Form | None,
        template_fragment: str | None = None,
        *,
        page_file_path: Path | None = None,
    ) -> str:
        """Render validation-error HTML for a page module path."""
        target_path = page_file_path
        if target_path is None:
            target_path = validated_next_form_page_path(request)
        if target_path is None:
            return ""
        return render_form_page_with_errors(
            self,
            request,
            action_name,
            form,
            template_fragment,
            target_path,
        )


class FormActionFactory:
    """Instantiates backends from merged `DEFAULT_FORM_ACTION_BACKENDS` entries."""

    @classmethod
    def create_backend(cls, config: dict[str, Any]) -> FormActionBackend:
        """Return a single backend instance for one settings entry.

        The `BACKEND` key must be present and resolve to a `FormActionBackend`
        subclass. The matching `next.E044` system check guarantees both before
        the factory runs in production.
        """
        backend_path = config["BACKEND"]
        backend_class = import_class_cached(backend_path)
        return cast("FormActionBackend", backend_class(config))


__all__ = [
    "ActionMeta",
    "FormActionBackend",
    "FormActionFactory",
    "FormActionOptions",
    "RegistryFormActionBackend",
]
