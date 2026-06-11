"""Declarative multi-step form wizard with a pluggable cache-backed backend."""

import hashlib
import types
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, cast

from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured

from next.conf import import_class_cached, next_framework_settings
from next.conf.signals import settings_reloaded

from .backends import ActionRegistration, _file_to_dotted_module
from .base import _registration_gate, _to_snake_case
from .manager import form_action_manager
from .registration import registration_diagnostics


if TYPE_CHECKING:
    from django.core.cache.backends.base import BaseCache
    from django.forms import Form as DjangoForm
    from django.http import HttpRequest, HttpResponse


def _ensure_session_key(request: "HttpRequest", *, create: bool) -> str:
    """Return the request session key, optionally creating a session for it."""
    session = getattr(request, "session", None)
    if session is None:
        return ""
    if session.session_key is None:
        if not create:
            return ""
        session.create()
    return cast("str", session.session_key)


class FormWizardBackend(ABC):
    """Persists FormWizard step drafts between requests, keyed by wizard id."""

    @abstractmethod
    def load(self, request: "HttpRequest", wizard_id: str) -> dict[str, Any]:
        """Return the `{step: cleaned_data}` mapping for the wizard, in step order."""

    @abstractmethod
    def save_step(
        self, request: "HttpRequest", wizard_id: str, step: str, data: dict[str, Any]
    ) -> None:
        """Persist cleaned data for a single step."""

    @abstractmethod
    def clear(self, request: "HttpRequest", wizard_id: str) -> None:
        """Drop every stored step for the wizard."""


class CacheFormWizardBackend(FormWizardBackend):
    """Stores drafts in the Django cache, namespaced by session and wizard id."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Read `CACHE_ALIAS` and `TIMEOUT` from the backend `OPTIONS`."""
        options = config.get("OPTIONS", {}) if isinstance(config, dict) else {}
        self._options: dict[str, Any] = options if isinstance(options, dict) else {}
        self.cache_alias: str = self._options.get("CACHE_ALIAS", "default")

    def _cache(self) -> "BaseCache":
        return caches[self.cache_alias]

    def _timeout(self) -> int | None:
        if "TIMEOUT" in self._options:
            return cast("int | None", self._options["TIMEOUT"])
        return getattr(settings, "SESSION_COOKIE_AGE", None)

    def _key(self, session_key: str, wizard_id: str) -> str:
        return f"next_wizard:{session_key}:{wizard_id}"

    def load(self, request: "HttpRequest", wizard_id: str) -> dict[str, Any]:
        """Return the cached `{step: cleaned_data}` mapping for the visitor."""
        session_key = _ensure_session_key(request, create=False)
        if not session_key:
            return {}
        return dict(self._cache().get(self._key(session_key, wizard_id), {}))

    def save_step(
        self, request: "HttpRequest", wizard_id: str, step: str, data: dict[str, Any]
    ) -> None:
        """Persist cleaned data for one step, creating a session when absent."""
        session_key = _ensure_session_key(request, create=True)
        if not session_key:
            msg = (
                "CacheFormWizardBackend requires Django sessions to key stored "
                "steps. Add django.contrib.sessions to INSTALLED_APPS and "
                "SessionMiddleware to MIDDLEWARE, or configure a custom backend "
                "in DEFAULT_FORM_WIZARD_BACKEND."
            )
            raise ImproperlyConfigured(msg)
        key = self._key(session_key, wizard_id)
        bucket = dict(self._cache().get(key, {}))
        bucket[step] = dict(data)
        self._cache().set(key, bucket, self._timeout())

    def clear(self, request: "HttpRequest", wizard_id: str) -> None:
        """Drop the cached drafts for the visitor."""
        session_key = _ensure_session_key(request, create=False)
        if session_key:
            self._cache().delete(self._key(session_key, wizard_id))


class WizardBackendManager:
    """Lazily instantiates the single configured wizard backend."""

    def __init__(self) -> None:
        """Initialise an empty backend cache."""
        self._backend: FormWizardBackend | None = None

    def reset(self) -> None:
        """Drop the cached backend so a fresh config takes effect."""
        self._backend = None

    def get(self) -> FormWizardBackend:
        """Return the backend configured by `DEFAULT_FORM_WIZARD_BACKEND`."""
        if self._backend is None:
            config = next_framework_settings.DEFAULT_FORM_WIZARD_BACKEND
            backend_class = import_class_cached(config["BACKEND"])
            self._backend = cast("FormWizardBackend", backend_class(config))
        return self._backend


wizard_backend_manager = WizardBackendManager()


def _on_settings_reloaded(**_kwargs: object) -> None:
    """Drop the cached backend so a reloaded config takes effect."""
    wizard_backend_manager.reset()


settings_reloaded.connect(_on_settings_reloaded)


def _replace_step_segment(path: str, current: str, target: str) -> str:
    """Return `path` with the segment naming `current` swapped for `target`."""
    if not path:
        return path
    parts = path.split("/")
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] == current:
            parts[index] = target
            return "/".join(parts)
    for index in range(len(parts) - 1, -1, -1):
        if parts[index]:
            parts[index] = target
            return "/".join(parts)
    return path


def _storage_scope_hash(scope_key: str) -> str:
    """Return a short stable hash of the registration scope key."""
    return hashlib.sha256(scope_key.encode()).hexdigest()[:16]


def _auto_register_wizard_class(cls: "type[FormWizard]") -> None:
    """Register a FormWizard subclass with form_action_manager."""
    gate = _registration_gate(cls)
    if gate is None:
        return
    scope, name, file_path = gate
    if not list(getattr(getattr(cls, "Meta", None), "steps", []) or []):
        registration_diagnostics.wizard_without_steps.append(cls.__qualname__)
    # Mirror the registry scope key so storage partitions match registration.
    cls._storage_scope_key = (
        file_path if scope == "page" else _file_to_dotted_module(file_path)
    )
    form_action_manager.register_action(
        ActionRegistration(
            name=name,
            file_path=file_path,
            scope=scope,
            wizard_class=cls,
        )
    )


class FormWizard:
    """Routes a sequence of forms across requests and finalises on the last step."""

    _storage_scope_key: ClassVar[str]

    class Meta:
        """Default wizard options, overridden on subclasses."""

        steps: ClassVar[list[tuple[str, type]]] = []
        url_param: str = "step"

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Register the wizard subclass automatically."""
        super().__init_subclass__(**kwargs)
        _auto_register_wizard_class(cls)

    def __init__(
        self,
        request: "HttpRequest",
        url_kwargs: dict[str, object] | None = None,
        base_path: str | None = None,
    ) -> None:
        """Bind the wizard to a request, its URL kwargs, and a page path."""
        self.request = request
        self.url_kwargs = dict(url_kwargs or {})
        if base_path is not None:
            self.base_path = base_path
        else:
            self.base_path = getattr(request, "path", "") or ""
        self.wizard_id = _to_snake_case(type(self).__name__)
        # Read the class's own namespace so an unregistered subclass never
        # borrows the storage bucket of a registered ancestor.
        scope_key = type(self).__dict__.get("_storage_scope_key") or type(
            self
        ).__module__
        self.storage_id = f"{_storage_scope_hash(scope_key)}:{self.wizard_id}"
        self._backend = wizard_backend_manager.get()
        self._loaded: dict[str, Any] | None = None
        self._steps_cache: list[tuple[str, type]] | None = None
        self._step_map_cache: dict[str, type] | None = None

    @classmethod
    def _meta(cls) -> object:
        return getattr(cls, "Meta", None)

    @classmethod
    def _static_steps(cls) -> list[tuple[str, type]]:
        return list(getattr(cls._meta(), "steps", []) or [])

    @property
    def url_param(self) -> str:
        """Return the URL kwarg name that carries the current step."""
        return getattr(self._meta(), "url_param", "step")

    def steps_for(self) -> list[tuple[str, type]]:
        """Return the step list. Override for conditional steps."""
        return self._static_steps()

    def get_form_kwargs(self) -> dict[str, Any]:
        """Return extra kwargs for a step form. Override for cross-step inputs."""
        return {}

    def _stored_steps(self) -> dict[str, Any]:
        """Return the stored `{step: data}` mapping, loaded once per request."""
        if self._loaded is None:
            self._loaded = self._backend.load(self.request, self.storage_id)
        return self._loaded

    def cleaned_data_so_far(self) -> dict[str, Any]:
        """Return the merged cleaned data of every stored step."""
        merged: dict[str, Any] = {}
        for data in self._stored_steps().values():
            merged.update(data)
        return merged

    def completed_steps(self) -> list[str]:
        """Return the names of steps that already have stored data."""
        return list(self._stored_steps())

    def save_step(self, step: str, data: dict[str, Any]) -> None:
        """Persist cleaned data for one step through the backend."""
        self._backend.save_step(self.request, self.storage_id, step, data)
        self._loaded = None
        self._invalidate_step_caches()

    def clear_storage(self) -> None:
        """Drop every stored step for this wizard through the backend."""
        self._backend.clear(self.request, self.storage_id)
        self._loaded = None
        self._invalidate_step_caches()

    def _invalidate_step_caches(self) -> None:
        """Drop cached steps so `steps_for` re-evaluates against fresh stored data."""
        self._steps_cache = None
        self._step_map_cache = None

    def _resolved_steps(self) -> list[tuple[str, type]]:
        """Return the `steps_for` output, cached until stored data changes."""
        if self._steps_cache is None:
            self._steps_cache = list(self.steps_for())
        return self._steps_cache

    def step_names(self) -> list[str]:
        """Return the ordered step names for this request."""
        return [name for name, _ in self._resolved_steps()]

    def current_step(self) -> str:
        """Return the active step from the URL kwarg, defaulting to the first."""
        names = self.step_names()
        raw = self.url_kwargs.get(self.url_param)
        if raw is not None and str(raw) in names:
            return str(raw)
        return names[0] if names else ""

    def is_first(self) -> bool:
        """Return True when the current step is the first step."""
        names = self.step_names()
        return bool(names) and self.current_step() == names[0]

    def is_last(self) -> bool:
        """Return True when the current step is the last step."""
        names = self.step_names()
        return bool(names) and self.current_step() == names[-1]

    def next_step(self, step: str | None = None) -> str | None:
        """Return the step following `step` (or the current step), or None."""
        names = self.step_names()
        target = step or self.current_step()
        if target not in names:
            return None
        index = names.index(target)
        return names[index + 1] if index + 1 < len(names) else None

    def goto(self, step: str) -> str:
        """Return the page URL for `step`, derived from the wizard page path."""
        return _replace_step_segment(self.base_path, self.current_step(), step)

    def step_form_class(self, step: str | None = None) -> type | None:
        """Return the form class registered for `step` (or the current step)."""
        target = step or self.current_step()
        if self._step_map_cache is None:
            self._step_map_cache = dict(self._resolved_steps())
        return self._step_map_cache.get(target)

    def current_form(self) -> "DjangoForm | None":
        """Return an unbound form for the current step, prefilled from storage."""
        step = self.current_step()
        form_class = self.step_form_class(step)
        if form_class is None:
            return None
        kwargs = dict(self.get_form_kwargs())
        stored = self._stored_steps().get(step)
        if stored is not None:
            kwargs.setdefault("initial", dict(stored))
        return cast("DjangoForm", form_class(**kwargs))

    def template_namespace(self) -> types.SimpleNamespace:
        """Return the `{form, wizard}` namespace consumed by the form tag."""
        return types.SimpleNamespace(form=self.current_form(), wizard=self)

    def done(
        self,
        request: "HttpRequest",
        cleaned_data: dict[str, Any],
    ) -> "HttpResponse":
        """Finalise the wizard after the last step. Subclasses must override."""
        msg = f"{type(self).__name__} must implement done(request, cleaned_data)."
        raise NotImplementedError(msg)


__all__ = [
    "CacheFormWizardBackend",
    "FormWizard",
    "FormWizardBackend",
    "WizardBackendManager",
    "wizard_backend_manager",
]
