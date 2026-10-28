"""Manager for form action backends and routing."""

import itertools
import types
from typing import TYPE_CHECKING, Any, override

from django.core.exceptions import ImproperlyConfigured

from next.backends import BackendListManager, backend_entries, load_backends
from next.introspect import refuse_async

from .backends import FormActionBackend, declaring_files
from .dispatch.build import _form_action_context_callable
from .errors import FormActionNotFoundError
from .origin import url_kwargs_for_request
from .signals import form_backend_loaded


if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.http import HttpRequest
    from django.urls import URLPattern

    from .backends import ActionMeta, ActionRegistration


type ActionsSnapshot = tuple[tuple[FormActionBackend, object], ...]
"""Per-backend opaque tokens, each paired with the backend that minted it."""


_version_counter = itertools.count(1)
"""Process-wide source of form-action versions, so no two managers share one."""

_FORM_HOOKS: tuple[str, ...] = (
    "get_initial",
    "get_form_kwargs",
    "check_permissions",
    "has_object_permission",
    "on_valid",
    "get_success_message",
)
"""Form methods the dispatcher calls, each refused at registration when async."""

_WIZARD_HOOKS: tuple[str, ...] = (
    "check_permissions",
    "get_steps",
    "get_form_kwargs",
    "done",
    "get_success_message",
)
"""Wizard methods the dispatcher calls, each refused at registration when async."""


def _refuse_async_hooks(target: type, hooks: tuple[str, ...]) -> None:
    """Refuse every hook of `target` among `hooks` that is declared `async def`."""
    for hook in hooks:
        method = getattr(target, hook, None)
        if callable(method):
            refuse_async(method, f"{target.__name__} hook")


def _refuse_async_registration(registration: "ActionRegistration") -> None:
    """Refuse an async handler, form hook or wizard hook before it is registered."""
    if registration.handler is not None:
        refuse_async(registration.handler, "@action handler")
    form_class = registration.form_class
    if isinstance(form_class, type):
        _refuse_async_hooks(form_class, _FORM_HOOKS)
    elif form_class is not None:
        refuse_async(form_class, "@action form_class factory")
    if registration.wizard_class is not None:
        _refuse_async_hooks(registration.wizard_class, _WIZARD_HOOKS)


class FormActionManager(BackendListManager[FormActionBackend]):
    """Holds one or more backends and yields their URL patterns."""

    def __init__(self, backends: "list[FormActionBackend] | None" = None) -> None:
        """Initialise with explicit backends or defer loading to settings."""
        self._version = next(_version_counter)
        super().__init__(backends)

    @property
    def version(self) -> int:
        """Cache token for the lazy urlpatterns concat, moved by every registration.

        Direct backend registrations are not tracked and the token is process-wide.
        """
        return self._version

    def _bump(self) -> None:
        self._version = next(_version_counter)

    @override
    def __repr__(self) -> str:
        """Return a debug representation showing the number of backends."""
        return f"<{self.__class__.__name__} backends={len(self._backends)}>"

    def __iter__(self) -> "Iterator[URLPattern]":
        """Yield concatenated URL patterns from each backend."""
        self._ensure_backends()
        for backend in self._backends:
            yield from backend.generate_urls()

    @override
    def reload(self) -> None:
        """Rebuild the backends from the current `NEXT_FRAMEWORK` settings.

        Actions on the old backends go with them, so forms must register again.
        """
        configs = backend_entries("FORM_ACTION_BACKENDS")
        self._bump()
        # No default, an entry without BACKEND is a next.E044 misconfiguration.
        self._backends = load_backends(
            configs, base=FormActionBackend, signal=form_backend_loaded
        )
        # An empty load is a broken config, so the next access rereads settings.
        self._mark_loaded(retry_when_empty=bool(configs))

    def _require_backends(self) -> None:
        """Load the backends and refuse a lookup that has none to consult."""
        self._ensure_backends()
        if not self._backends:
            raise ImproperlyConfigured(self._no_backends_message())

    def _first_backend(self) -> "FormActionBackend":
        """Return the first backend or raise when none are configured."""
        self._require_backends()
        return self._backends[0]

    def _no_backends_message(self) -> str:
        """Tell an unconfigured family apart from one whose entries all failed."""
        attempted = len(backend_entries("FORM_ACTION_BACKENDS"))
        if attempted:
            return (
                f"None of the {attempted} entries in "
                "NEXT_FRAMEWORK['FORM_ACTION_BACKENDS'] could be loaded. The "
                "next.backends logger names why each one was skipped."
            )
        return (
            "No form action backends configured. Add at least one entry to "
            "NEXT_FRAMEWORK['FORM_ACTION_BACKENDS']."
        )

    def register_action(self, registration: "ActionRegistration") -> None:
        """Refuse async callables, then forward registration to the first backend."""
        _refuse_async_registration(registration)
        self._first_backend().register_action(registration)
        self._bump()

    def clear_registries(self) -> None:
        """Clear the action storage of every backend. For test isolation."""
        self._bump()
        for backend in self._backends:
            backend.clear_registry()

    def snapshot_actions(self) -> "ActionsSnapshot":
        """Capture the actions of every backend for a later `restore_actions`.

        Each token returns to the backend that minted it.
        """
        self._ensure_backends()
        return tuple((backend, backend.snapshot()) for backend in self._backends)

    def restore_actions(self, snapshot: "ActionsSnapshot") -> None:
        """Put the captured actions back, moving `version` as a registration does.

        A rollback changes what is registered, so token-keyed caches must see it.
        """
        for backend, state in snapshot:
            backend.restore(state)
        self._bump()

    def get_action_url(self, action_name: str, *, page_path: str | None = None) -> str:
        """Return the reverse URL from the first backend that knows `action_name`.

        The refusal is composed here, so it never depends on the backend count.
        """
        self._require_backends()
        backends = self._backends
        if len(backends) == 1:
            return backends[0].get_action_url(action_name, page_path=page_path)
        registry_empty = True
        for backend in backends:
            try:
                return backend.get_action_url(action_name, page_path=page_path)
            except FormActionNotFoundError as exc:
                if not exc.registry_empty:
                    registry_empty = False
        raise FormActionNotFoundError(
            name=action_name,
            page_path=page_path,
            candidates=lambda: [
                meta["name"] for backend in backends for meta in backend.iter_actions()
            ],
            registry_empty=registry_empty,
            declared_in=lambda: declaring_files(backends, action_name),
        )

    def get_action_meta(
        self, action_name: str, *, page_path: str | None = None
    ) -> "ActionMeta | None":
        """Return the action meta from the first backend that knows the name."""
        self._require_backends()
        for backend in self._backends:
            meta = backend.get_meta(action_name, page_path)
            if meta is not None:
                return meta
        return None

    def require_action_meta(
        self, action_name: str, *, page_path: str | None = None
    ) -> "ActionMeta":
        """Return the action meta or raise with close matches when none exists."""
        meta = self.get_action_meta(action_name, page_path=page_path)
        if meta is not None:
            return meta
        known = {
            registered_name
            for backend in self.backends
            for registered in backend.iter_actions()
            if (registered_name := registered.get("name")) is not None
        }
        raise FormActionNotFoundError(
            name=action_name,
            page_path=page_path,
            candidates=tuple(known),
            registry_empty=not known,
            declared_in=lambda: declaring_files(self.backends, action_name),
        )

    @property
    def backends(self) -> "tuple[FormActionBackend, ...]":
        """Return the configured backends in consultation order."""
        self._ensure_backends()
        return tuple(self._backends)

    @property
    def default_backend(self) -> "FormActionBackend":
        """Return the first configured backend."""
        return self._first_backend()


form_action_manager = FormActionManager()


def resolve_component_anchor(
    action_name: str, component_path: str
) -> "ActionMeta | None":
    """Return the action meta registered exactly under the component anchor.

    An exact hit carries page scope, unlike the shared fallback of a scoped lookup.
    """
    meta = form_action_manager.get_action_meta(action_name, page_path=component_path)
    if meta is not None and meta.get("scope") == "page":
        return meta
    return None


def build_form_namespace_for_action(
    action_name: str, request: "HttpRequest", page_path: str | None = None
) -> types.SimpleNamespace | None:
    """Build the form namespace used by the form template tag."""
    meta = form_action_manager.get_action_meta(action_name, page_path=page_path)
    if meta is None:
        return None
    return _build_form_namespace_from_meta(meta, request)


def _build_form_namespace_from_meta(
    meta: "ActionMeta", request: "HttpRequest", dep_cache: dict[str, Any] | None = None
) -> types.SimpleNamespace | None:
    """Build the form namespace for already-resolved action meta."""
    wizard_class = meta.get("wizard_class")
    if wizard_class is not None:
        url_kwargs = url_kwargs_for_request(request)
        wizard = wizard_class(request=request, url_kwargs=url_kwargs)
        return wizard.template_namespace()
    fc = meta.get("form_class")
    if fc is None:
        return None
    return _form_action_context_callable(fc)(request, dep_cache)


__all__ = [
    "FormActionManager",
    "build_form_namespace_for_action",
    "form_action_manager",
    "resolve_component_anchor",
]
