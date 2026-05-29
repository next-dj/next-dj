"""Backend abstractions and in-memory registry for form actions."""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponseNotFound
from django.urls import path, reverse
from django.urls.exceptions import NoReverseMatch
from django.views.decorators.http import require_http_methods

from next.conf import import_class_cached

from .dispatch import FormActionDispatch
from .rendering import render_form_page_with_errors
from .signals import action_registered
from .uid import (
    FORM_ACTION_REVERSE_NAME,
    URL_NAME_FORM_ACTION,
    validated_next_form_page_path,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from django import forms as django_forms
    from django.http import HttpRequest, HttpResponse
    from django.urls import URLPattern


def _file_to_dotted_module(file_path: str) -> str:
    """Return a dotted module name by walking up while __init__.py exists."""
    p = Path(file_path).resolve()
    parts: list[str] = []
    current = p.with_suffix("")
    while True:
        init = current.parent / "__init__.py"
        if not init.exists():
            break
        parts.append(current.name)
        current = current.parent
    if not parts:
        return p.stem
    parts.reverse()
    return ".".join(parts)


_action_collisions: dict[str, set[tuple[str, str]]] = {}


def _handler_fingerprint(handler: "Callable[..., Any]") -> tuple[str, str]:
    module = getattr(handler, "__module__", "") or ""
    qualname = getattr(handler, "__qualname__", "") or getattr(handler, "__name__", "")
    return (str(module), str(qualname))


def record_possible_collision(
    action_name: str,
    old_handler: "Callable[..., Any]",
    new_handler: "Callable[..., Any]",
) -> None:
    """Record a collision when a name is re-registered with a distinct handler."""
    if old_handler is new_handler:
        return
    old_fp = _handler_fingerprint(old_handler)
    new_fp = _handler_fingerprint(new_handler)
    if old_fp == new_fp:
        return
    _action_collisions.setdefault(action_name, {old_fp}).add(new_fp)


def clear_action_collisions() -> None:
    """Drop the collision-check state. Intended for test isolation."""
    _action_collisions.clear()


class ActionMeta(TypedDict, total=False):
    """Per-action data stored in the registry backend."""

    handler: "Callable[..., Any] | None"
    form_class: "type[django_forms.Form] | Callable[..., Any] | None"
    wizard_class: "type | None"
    uid: str
    file_path: str
    scope: str


@dataclass
class FormActionOptions:
    """Options for register_action. Extend to add backend-specific settings."""


class FormActionBackend(ABC):
    """Storage and HTTP dispatch for `@action` handlers."""

    @abstractmethod
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
        """Record an action from the decorator or __init_subclass__."""

    @abstractmethod
    def get_action_url(self, action_name: str, *, page_path: str | None = None) -> str:
        """Return the reverse URL for `action_name`."""

    @abstractmethod
    def generate_urls(self) -> "list[URLPattern]":
        """Return URLconf entries for this backend."""

    @abstractmethod
    def dispatch(self, request: "HttpRequest", uid: str) -> "HttpResponse":
        """Run the handler for `uid`."""

    def get_meta(
        self,
        action_name: str,  # noqa: ARG002
        *,
        page_path: str | None = None,  # noqa: ARG002
    ) -> "dict[str, Any] | None":
        """Return optional per-action metadata for subclasses."""
        return None

    def render_form_fragment(
        self,
        request: "HttpRequest",  # noqa: ARG002
        action_name: str,  # noqa: ARG002
        form: "django_forms.Form | None",  # noqa: ARG002
        template_fragment: str | None = None,  # noqa: ARG002
        *,
        page_file_path: "Path | None" = None,  # noqa: ARG002
    ) -> str:
        """Return custom HTML for validation errors (override in subclasses)."""
        return ""


def _make_uid_for_action(scope_key: str, name: str) -> str:
    """Return a stable short id derived from scope_key and action name."""
    reverse_name = f"next:form:{scope_key}:{name}"
    raw = reverse_name.encode()
    return hashlib.sha256(raw).hexdigest()[:16]


class RegistryFormActionBackend(FormActionBackend):
    """In-memory actions behind one dispatcher path keyed by UID."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:  # noqa: ARG002
        """Create an empty action map. `config` is accepted for factory parity."""
        self._registry: dict[tuple[str, str], ActionMeta] = {}
        self._uid_to_name: dict[str, tuple[str, str]] = {}
        self._name_index: dict[str, tuple[str, str]] = {}

    def clear_registry(self) -> None:
        """Drop every registered action and reset the UID index. For test isolation."""
        self._registry.clear()
        self._uid_to_name.clear()
        self._name_index.clear()

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
        """Store handler, form_class, or wizard_class and a stable uid for the name."""
        if scope == "page":
            scope_key = str(Path(file_path).resolve())
        else:
            scope_key = _file_to_dotted_module(file_path)

        uid = _make_uid_for_action(scope_key, name)
        existing_key = self._uid_to_name.get(uid)
        if existing_key is not None and existing_key != (scope_key, name):
            msg = (
                f"Form action UID collision: {existing_key!r} and "
                f"({scope_key!r}, {name!r}) both hash to {uid!r}. "
                "Rename one of them."
            )
            raise ImproperlyConfigured(msg)
        self._uid_to_name[uid] = (scope_key, name)

        key = (scope_key, name)
        previous = self._registry.get(key)
        if previous is not None:
            old_obj = (
                previous.get("handler")
                or previous.get("form_class")
                or previous.get("wizard_class")
            )
            new_obj = handler or form_class or wizard_class
            if old_obj is not None and new_obj is not None:
                record_possible_collision(f"{scope_key}:{name}", old_obj, new_obj)

        self._registry[key] = {
            "handler": handler,
            "form_class": form_class,
            "wizard_class": wizard_class,
            "uid": uid,
            "file_path": file_path,
            "scope": scope,
        }
        self._name_index.setdefault(name, key)
        action_registered.send(
            sender=self.__class__,
            action_name=name,
            uid=uid,
            form_class=form_class,
            file_path=file_path,
            scope=scope,
            handler=handler,
        )

    def get_action_url(self, action_name: str, *, page_path: str | None = None) -> str:
        """Return the reverse URL for a registered action name."""
        if page_path is not None:
            key = (str(Path(page_path).resolve()), action_name)
            if key in self._registry:
                uid = self._registry[key]["uid"]
                if uid is not None:
                    try:
                        return reverse(FORM_ACTION_REVERSE_NAME, kwargs={"uid": uid})
                    except NoReverseMatch:
                        return reverse(URL_NAME_FORM_ACTION, kwargs={"uid": uid})

        fallback_key = self._name_index.get(action_name)
        if fallback_key is not None:
            uid = self._registry[fallback_key]["uid"]
            if uid is not None:
                try:
                    return reverse(FORM_ACTION_REVERSE_NAME, kwargs={"uid": uid})
                except NoReverseMatch:
                    return reverse(URL_NAME_FORM_ACTION, kwargs={"uid": uid})

        msg = f"Unknown form action: {action_name}"
        raise KeyError(msg)

    def generate_urls(self) -> "list[URLPattern]":
        """Return one catch-all route when at least one action is registered."""
        if not self._registry:
            return []
        view = require_http_methods(["GET", "POST"])(self.dispatch)
        return [path("_next/form/<str:uid>/", view, name=URL_NAME_FORM_ACTION)]

    def dispatch(self, request: "HttpRequest", uid: str) -> "HttpResponse":
        """Forward a POST request to `FormActionDispatch.dispatch`."""
        key = self._uid_to_name.get(uid)
        if key is None or key not in self._registry:
            return HttpResponseNotFound()
        meta = self._registry[key]
        return FormActionDispatch.dispatch(
            self, request, key[1], cast("dict[str, Any]", meta)
        )

    def get_meta(
        self,
        action_name: str,
        *,
        page_path: str | None = None,
    ) -> "dict[str, Any] | None":
        """Return stored `ActionMeta` for the name, if any."""
        if page_path is not None:
            key = (str(Path(page_path).resolve()), action_name)
            meta = self._registry.get(key)
            if meta is not None:
                return cast("dict[str, Any]", meta)

        fallback_key = self._name_index.get(action_name)
        if fallback_key is not None:
            return cast("dict[str, Any]", self._registry[fallback_key])

        return None

    def render_form_fragment(
        self,
        request: "HttpRequest",
        action_name: str,
        form: "django_forms.Form | None",
        template_fragment: str | None = None,
        *,
        page_file_path: "Path | None" = None,
    ) -> str:
        """Render validation-error HTML for a page module path."""
        del template_fragment
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
            target_path,
        )


class FormActionFactory:
    """Instantiates backends from merged `DEFAULT_FORM_ACTION_BACKENDS` entries."""

    @classmethod
    def create_backend(cls, config: dict[str, Any]) -> FormActionBackend:
        """Return a single backend instance for one settings entry."""
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
