"""Backend abstractions and in-memory registry for form actions."""

import difflib
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast
from weakref import WeakSet

from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.http import HttpResponseNotFound
from django.urls import get_script_prefix, path
from django.views.decorators.http import require_http_methods

from next.conf import import_class_cached

from .dispatch import ActionOutcome, FormActionDispatch
from .exceptions import FormActionNotFound, _unknown_action_message
from .registration import registration_diagnostics
from .rendering import _ErrorRenderParams, render_form_page_with_errors
from .signals import action_registered
from .uid import URL_NAME_FORM_ACTION, reverse_form_action


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

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


def file_to_dotted_module(file_path: str) -> str:
    """Return a dotted module name by walking up while __init__.py exists."""
    cached = _dotted_module_cache.get(file_path)
    if cached is not None:
        return cached
    p = Path(_resolved_path_str(file_path))
    parts: list[str] = [p.stem]
    directory = p.parent
    while (directory / "__init__.py").exists():
        parts.append(directory.name)
        directory = directory.parent
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


@dataclass(frozen=True, slots=True)
class ActionGuard:
    """Access requirements enforced before a form action dispatches."""

    login_required: bool = False
    permissions: tuple[str, ...] = ()


def build_action_guard(
    *,
    login_required: bool = False,
    permission_required: "str | Iterable[str] | None" = None,
) -> ActionGuard | None:
    """Return an `ActionGuard` for the declared requirements, or None when unset."""
    if permission_required is None:
        permissions: tuple[str, ...] = ()
    elif isinstance(permission_required, str):
        permissions = (permission_required,)
    else:
        permissions = tuple(permission_required)
    if not login_required and not permissions:
        return None
    return ActionGuard(login_required=bool(login_required), permissions=permissions)


class ActionMeta(TypedDict, total=False):
    """Per-action data stored in the registry backend."""

    name: str
    handler: "Callable[..., Any] | None"
    form_class: "type[django_forms.Form] | Callable[..., Any] | None"
    wizard_class: "type | None"
    uid: str
    file_path: str
    scope: str
    guard: ActionGuard | None


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
    guard: ActionGuard | None = None


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
    ) -> "ActionMeta | None":
        """Return optional per-action metadata for subclasses."""
        return None

    def iter_actions(self) -> "Iterable[ActionMeta]":
        """Yield the metadata of every action this backend owns."""
        return ()

    def render_invalid_page(
        self,
        _request: "HttpRequest",
        _action_name: str,
        _form: "django_forms.Form | None",
        _page_file_path: "Path | None" = None,
        _url_kwargs: dict[str, object] | None = None,
    ) -> str:
        """Return the full origin-page HTML for a failed validation."""
        return ""

    def shape_response(
        self,
        request: "HttpRequest",
        outcome: ActionOutcome,
    ) -> "HttpResponse":
        """Turn one pipeline outcome into the HTTP response. Default envelope."""
        return FormActionDispatch.shape_response(self, request, outcome)


def _make_uid_for_action(scope_key: str, name: str) -> str:
    """Return a stable short id derived from scope_key and action name."""
    reverse_name = f"next:form:{scope_key}:{name}"
    raw = reverse_name.encode()
    return hashlib.sha256(raw).hexdigest()[:16]


_url_caching_backends: "WeakSet[RegistryFormActionBackend]" = WeakSet()


def _on_setting_changed(*, setting: str, **_kwargs: object) -> None:
    """Drop cached action URLs when the URLconf is swapped under override_settings."""
    if setting == "ROOT_URLCONF":
        for backend in _url_caching_backends:
            backend._url_cache.clear()


setting_changed.connect(_on_setting_changed)


class RegistryFormActionBackend(FormActionBackend):
    """In-memory actions behind one dispatcher path keyed by UID."""

    def __init__(self, _config: dict[str, Any] | None = None) -> None:
        """Create an empty action map. `_config` is accepted for factory parity."""
        self._registry: dict[tuple[str, str], ActionMeta] = {}
        self._uid_to_name: dict[str, tuple[str, str]] = {}
        self._name_index: dict[str, tuple[str, str]] = {}
        self._url_cache: dict[tuple[str, str], str] = {}
        _url_caching_backends.add(self)

    def clear_registry(self) -> None:
        """Drop every registered action and reset the UID index. For test isolation."""
        self._registry.clear()
        self._uid_to_name.clear()
        self._name_index.clear()
        self._url_cache.clear()

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
            scope_key = file_to_dotted_module(file_path)

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
            "name": name,
            "handler": handler,
            "form_class": form_class,
            "wizard_class": wizard_class,
            "uid": uid,
            "file_path": file_path,
            "scope": scope,
            "guard": registration.guard,
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

    def _fallback_meta(self, action_name: str, *, scoped: bool) -> ActionMeta | None:
        """Return the name-index entry, shared-scope only for page-scoped lookups."""
        fallback_key = self._name_index.get(action_name)
        if fallback_key is None:
            return None
        meta = self._registry.get(fallback_key)
        if meta is None:
            return None
        if scoped and meta.get("scope") != "shared":
            return None
        return meta

    def get_action_url(self, action_name: str, *, page_path: str | None = None) -> str:
        """Return the reverse URL for a registered action name."""
        meta: ActionMeta | None = None
        if page_path is not None:
            meta = self._registry.get((_resolved_path_str(page_path), action_name))
        if meta is None:
            meta = self._fallback_meta(action_name, scoped=page_path is not None)
        if meta is not None:
            uid = meta.get("uid")
            if uid is not None:
                # The script prefix is request-scoped state and reverse() bakes
                # it into the URL, so it must be part of the cache key.
                cache_key = (get_script_prefix(), uid)
                url = self._url_cache.get(cache_key)
                if url is None:
                    url = reverse_form_action(uid)
                    self._url_cache[cache_key] = url
                return url

        suggestions = tuple(
            difflib.get_close_matches(
                action_name,
                sorted({name for _scope_key, name in self._registry}),
            )
        )
        raise FormActionNotFound(
            _unknown_action_message(action_name, page_path, suggestions),
            name=action_name,
            page_path=page_path,
            suggestions=suggestions,
        )

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
        return FormActionDispatch.dispatch(self, request, key[1], meta)

    def get_meta(
        self,
        action_name: str,
        page_path: str | None = None,
    ) -> "ActionMeta | None":
        """Return stored `ActionMeta` for the name, if any."""
        if page_path is not None:
            key = (_resolved_path_str(page_path), action_name)
            meta = self._registry.get(key)
            if meta is not None:
                return meta

        return self._fallback_meta(action_name, scoped=page_path is not None)

    def iter_actions(self) -> "Iterable[ActionMeta]":
        """Yield every stored `ActionMeta` in registration order."""
        yield from self._registry.values()

    def render_invalid_page(
        self,
        request: "HttpRequest",
        action_name: str,
        form: "django_forms.Form | None",
        page_file_path: "Path | None" = None,
        url_kwargs: dict[str, object] | None = None,
    ) -> str:
        """Render validation-error HTML for a page module path."""
        if page_file_path is None:
            return ""
        params = _ErrorRenderParams(
            action_name=action_name,
            form=form,
            url_kwargs=url_kwargs if url_kwargs is not None else {},
        )
        return render_form_page_with_errors(self, request, params, page_file_path)


class FormActionFactory:
    """Instantiates backends from merged `FORM_ACTION_BACKENDS` entries."""

    @classmethod
    def create_backend(cls, config: dict[str, Any]) -> FormActionBackend:
        """Return a single backend instance for one settings entry."""
        backend_path = config["BACKEND"]
        backend_class = import_class_cached(backend_path)
        return cast("FormActionBackend", backend_class(config))


__all__ = [
    "ActionGuard",
    "ActionMeta",
    "ActionRegistration",
    "FormActionBackend",
    "FormActionFactory",
    "RegistryFormActionBackend",
    "build_action_guard",
    "file_to_dotted_module",
]
