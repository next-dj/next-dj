"""Backend abstractions and in-memory registry for form actions."""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponseNotFound
from django.urls import path
from django.views.decorators.http import require_http_methods

from next.conf import import_class_cached

from .dispatch import FormActionDispatch
from .registration import registration_diagnostics
from .rendering import render_form_page_with_errors
from .signals import action_registered
from .uid import (
    URL_NAME_FORM_ACTION,
    reverse_form_action,
    validated_next_form_page_path,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from django import forms as django_forms
    from django.http import HttpRequest, HttpResponse
    from django.urls import URLPattern


# Memoised by raw path: both hit a filesystem syscall on every registration and
# scoped lookup. No invalidation needed since resolve() is process-stable.
_resolved_path_cache: dict[str, str] = {}
_dotted_module_cache: dict[str, str] = {}


def _resolved_path_str(file_path: str) -> str:
    """Return the resolved absolute path string, memoised by raw path."""
    resolved = _resolved_path_cache.get(file_path)
    if resolved is None:
        resolved = str(Path(file_path).resolve())
        _resolved_path_cache[file_path] = resolved
    return resolved


def _file_to_dotted_module(file_path: str) -> str:
    """Return a dotted module name by walking up while __init__.py exists."""
    cached = _dotted_module_cache.get(file_path)
    if cached is not None:
        return cached
    p = Path(_resolved_path_str(file_path))
    parts: list[str] = []
    current = p.with_suffix("")
    while True:
        init = current.parent / "__init__.py"
        if not init.exists():
            break
        parts.append(current.name)
        current = current.parent
    if not parts:
        dotted = p.stem
    else:
        parts.reverse()
        dotted = ".".join(parts)
    _dotted_module_cache[file_path] = dotted
    return dotted


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
    registration_diagnostics.action_collisions.setdefault(action_name, {old_fp}).add(
        new_fp
    )


class ActionMeta(TypedDict, total=False):
    """Per-action data stored in the registry backend."""

    handler: "Callable[..., Any] | None"
    form_class: "type[django_forms.Form] | Callable[..., Any] | None"
    wizard_class: "type | None"
    uid: str
    file_path: str
    scope: str


@dataclass(frozen=True)
class ActionRegistration:
    """A form action to register: its name, declaration site, and target.

    Exactly one of `handler`, `form_class`, or `wizard_class` is the action
    target, except the `@action(form_class=...)` path which supplies a handler
    and a form-factory together.
    """

    name: str
    file_path: str
    scope: str
    handler: "Callable[..., Any] | None" = None
    form_class: "type[django_forms.Form] | Callable[..., Any] | None" = None
    wizard_class: "type | None" = None


class FormActionBackend(ABC):
    """Storage and HTTP dispatch for `@action` handlers."""

    @abstractmethod
    def register_action(self, registration: ActionRegistration) -> None:
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
        _action_name: str,
        _page_path: str | None = None,
    ) -> "dict[str, Any] | None":
        """Return optional per-action metadata for subclasses."""
        return None

    def render_form_fragment(
        self,
        _request: "HttpRequest",
        _action_name: str,
        _form: "django_forms.Form | None",
        _page_file_path: "Path | None" = None,
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

    def __init__(self, _config: dict[str, Any] | None = None) -> None:
        """Create an empty action map. `_config` is accepted for factory parity."""
        self._registry: dict[tuple[str, str], ActionMeta] = {}
        self._uid_to_name: dict[str, tuple[str, str]] = {}
        self._name_index: dict[str, tuple[str, str]] = {}

    def clear_registry(self) -> None:
        """Drop every registered action and reset the UID index. For test isolation."""
        self._registry.clear()
        self._uid_to_name.clear()
        self._name_index.clear()

    def register_action(self, registration: ActionRegistration) -> None:
        """Store handler, form_class, or wizard_class and a stable uid for the name."""
        name = registration.name
        file_path = registration.file_path
        scope = registration.scope
        handler = registration.handler
        form_class = registration.form_class
        wizard_class = registration.wizard_class
        if scope == "page":
            scope_key = _resolved_path_str(file_path)
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
        key: tuple[str, str] | None = None
        if page_path is not None:
            scoped_key = (_resolved_path_str(page_path), action_name)
            if scoped_key in self._registry:
                key = scoped_key
        if key is None:
            key = self._name_index.get(action_name)
        if key is not None:
            uid = self._registry[key]["uid"]
            if uid is not None:
                return reverse_form_action(uid)

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
        page_path: str | None = None,
    ) -> "dict[str, Any] | None":
        """Return stored `ActionMeta` for the name, if any."""
        if page_path is not None:
            key = (_resolved_path_str(page_path), action_name)
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
        page_file_path: "Path | None" = None,
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
    "ActionRegistration",
    "FormActionBackend",
    "FormActionFactory",
    "RegistryFormActionBackend",
]
