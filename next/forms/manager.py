"""Manager for form action backends and routing."""

import types
from typing import TYPE_CHECKING, override

from django.core.exceptions import ImproperlyConfigured

from next.backends import backend_entries, load_backends

from .backends import FormActionBackend, FormActionNotFoundError
from .dispatch.build import _form_action_context_callable
from .origin import _url_kwargs_for_request


if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.http import HttpRequest
    from django.urls import URLPattern

    from .backends import ActionMeta, ActionRegistration


type ActionsSnapshot = tuple[tuple[FormActionBackend, object], ...]
"""Per-backend opaque tokens, each paired with the backend that minted it."""


class FormActionManager:
    """Holds one or more backends and yields their URL patterns."""

    version: int = 0
    """Cache token for the lazy urlpatterns concat. Registrations that
    bypass the manager and hit a backend directly are not tracked, as
    they were never supported.

    Read it to key a cache of your own on the registered actions. It is a
    plain attribute rather than a property because the lazy urlpatterns
    concat reads it on every resolve."""

    def __init__(self, backends: "list[FormActionBackend] | None" = None) -> None:
        """Initialise with explicit backends or defer loading to settings."""
        self._backends: list[FormActionBackend] = list(backends) if backends else []
        # An empty list is a legitimate load result, so only a flag knows.
        self._loaded: bool = bool(self._backends)

    @override
    def __repr__(self) -> str:
        """Return a debug representation showing the number of backends."""
        return f"<{self.__class__.__name__} backends={len(self._backends)}>"

    def __iter__(self) -> "Iterator[URLPattern]":
        """Yield concatenated URL patterns from each backend."""
        self._ensure_backends()
        for backend in self._backends:
            yield from backend.generate_urls()

    def reload(self) -> None:
        """Rebuild the backends from the current `NEXT_FRAMEWORK` settings.

        The actions registered against the old backends go with them, so a
        caller that swaps `FORM_ACTION_BACKENDS` under a live manager lets
        the forms register again afterwards.
        """
        configs = backend_entries("FORM_ACTION_BACKENDS")
        self.version += 1
        # No default, an entry without BACKEND is a next.E044 misconfiguration.
        self._backends = load_backends(configs, base=FormActionBackend)
        # An empty load is a broken config, not a result, so the next access
        # rereads settings. A settings_reloaded receiver cannot do that instead,
        # because dropping the backends drops their actions.
        self._loaded = bool(self._backends) or not configs

    def _ensure_backends(self) -> None:
        if not self._loaded:
            self.reload()

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
        """Forward registration to the first backend."""
        self._first_backend().register_action(registration)
        self.version += 1

    def clear_registries(self) -> None:
        """Clear the action storage of every backend. For test isolation."""
        self.version += 1
        for backend in self._backends:
            backend.clear_registry()

    def snapshot_actions(self) -> "ActionsSnapshot":
        """Capture the actions of every backend for a later `restore_actions`.

        Each token travels back to the backend that minted it, so a list that
        changed in between restores the backends it still holds.
        """
        self._ensure_backends()
        return tuple((backend, backend.snapshot()) for backend in self._backends)

    def restore_actions(self, snapshot: "ActionsSnapshot") -> None:
        """Put the captured actions back, moving `version` as a registration does.

        A rollback changes what is registered, so a cache keyed on the token
        has to see it exactly as it sees a registration.
        """
        for backend, state in snapshot:
            backend.restore(state)
        self.version += 1

    def get_action_url(self, action_name: str, *, page_path: str | None = None) -> str:
        """Return the reverse URL from the first backend that knows `action_name`."""
        self._require_backends()
        if len(self._backends) == 1:
            return self._backends[0].get_action_url(action_name, page_path=page_path)
        caught: list[FormActionNotFoundError] = []
        for backend in self._backends:
            try:
                return backend.get_action_url(action_name, page_path=page_path)
            except FormActionNotFoundError as exc:
                caught.append(exc)
        raise FormActionNotFoundError(
            name=action_name,
            page_path=page_path,
            candidates=tuple(name for exc in caught for name in exc.candidates),
            registry_empty=all(exc.registry_empty for exc in caught),
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

    An exact anchor hit carries page scope, which tells it apart from the
    path-independent shared fallback a scoped lookup may return.
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
    meta: "ActionMeta", request: "HttpRequest"
) -> types.SimpleNamespace | None:
    """Build the form namespace for already-resolved action meta."""
    wizard_class = meta.get("wizard_class")
    if wizard_class is not None:
        url_kwargs = _url_kwargs_for_request(request)
        wizard = wizard_class(request=request, url_kwargs=url_kwargs)
        return wizard.template_namespace()
    fc = meta.get("form_class")
    if fc is None:
        return None
    return _form_action_context_callable(fc)(request)


__all__ = [
    "FormActionManager",
    "build_form_namespace_for_action",
    "form_action_manager",
    "resolve_component_anchor",
]
