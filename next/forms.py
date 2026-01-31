"""Form actions for next-dj.

Register handlers with @forms.action. Each action gets a unique UID endpoint.
Handlers run only when the form is valid. Otherwise the form is re-rendered
with errors. CSRF token is inserted in forms automatically.
"""

import hashlib
import inspect
import types
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict, cast

from django import forms as django_forms
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    HttpResponseNotFound,
    HttpResponseRedirect,
)
from django.template import Context, Template
from django.urls import URLPattern, path, reverse
from django.urls.exceptions import NoReverseMatch
from django.views.decorators.http import require_http_methods

from .pages import page


# Re-export common Django form classes for convenience
Form = django_forms.Form
CharField = django_forms.CharField
EmailField = django_forms.EmailField
IntegerField = django_forms.IntegerField
BooleanField = django_forms.BooleanField
ChoiceField = django_forms.ChoiceField
TypedChoiceField = django_forms.TypedChoiceField
MultipleChoiceField = django_forms.MultipleChoiceField
DateField = django_forms.DateField
DateTimeField = django_forms.DateTimeField
DecimalField = django_forms.DecimalField
FloatField = django_forms.FloatField
URLField = django_forms.URLField
RegexField = django_forms.RegexField
FileField = django_forms.FileField
ImageField = django_forms.ImageField
ModelForm = django_forms.ModelForm
ValidationError = django_forms.ValidationError


URL_NAME_FORM_ACTION = "form_action"

# When next.urls is included with app_name "next", reverse must use this.
FORM_ACTION_REVERSE_NAME = "next:form_action"


class ActionMeta(TypedDict, total=False):
    """Per-action data."""

    handler: Callable[..., Any]
    form_class: type[django_forms.Form] | None
    file_path: Path
    initial: Callable[[HttpRequest], dict[str, Any]] | None
    uid: str


@dataclass
class FormActionOptions:
    """Options for @action decorator."""

    form_class: type[django_forms.Form] | None = None
    initial: Callable[[HttpRequest], dict[str, Any]] | None = None
    file_path: Path | None = None


class FormActionBackend(ABC):
    """Backend for form actions. Plug registry, config or dynamic source."""

    @abstractmethod
    def register_action(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        options: FormActionOptions | None = None,
    ) -> None:
        """Register one action. Used by @action decorator."""

    @abstractmethod
    def get_action_url(self, action_name: str) -> str:
        """URL for that action. KeyError if unknown."""

    @abstractmethod
    def generate_urls(self) -> list[URLPattern]:
        """URL patterns for all registered actions."""

    @abstractmethod
    def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
        """Handle GET/POST by uid. 404 if uid unknown."""

    def get_meta(self, action_name: str) -> dict[str, Any] | None:  # noqa: ARG002
        """Metadata for action or None. Override in custom backends."""
        return None

    def render_form_fragment(
        self,
        request: HttpRequest,  # noqa: ARG002
        action_name: str,  # noqa: ARG002
        form: django_forms.Form | None,  # noqa: ARG002
        template_fragment: str | None = None,  # noqa: ARG002
    ) -> str:
        """HTML fragment for re-display. Override for custom rendering."""
        return ""


def _make_uid(file_path: Path, action_name: str) -> str:
    """Stable short id from file path and action name."""
    raw = f"{file_path!s}:{action_name}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _get_caller_path(back_count: int = 1) -> Path:
    """Path of the module that called into us. Skips frames from this file."""
    frame = inspect.currentframe()
    msg = "Could not determine caller file path"
    # Step back `back_count` frames (e.g. past decorator)
    for _ in range(back_count):
        if not frame or not frame.f_back:
            raise RuntimeError(msg)
        frame = frame.f_back
    # Walk up until we leave our own forms.py
    for _ in range(15):
        if not frame:
            break
        if (fpath := frame.f_globals.get("__file__")) and not fpath.endswith(
            "forms.py"
        ):
            return Path(fpath)
        frame = frame.f_back
    raise RuntimeError(msg)


class RegistryFormActionBackend(FormActionBackend):
    """In-memory backend. One URL pattern serves all actions by uid."""

    def __init__(self) -> None:
        """Empty registry and uid->name map."""
        self._registry: dict[str, ActionMeta] = {}
        self._uid_to_name: dict[str, str] = {}

    def register_action(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        options: FormActionOptions | None = None,
    ) -> None:
        """Store action and form/initial. Registers context when form set."""
        opts = options or FormActionOptions()
        fp = opts.file_path or _get_caller_path(2)
        uid = _make_uid(fp, name)
        self._uid_to_name[uid] = name
        self._registry[name] = {
            "handler": handler,
            "form_class": opts.form_class,
            "file_path": fp,
            "initial": opts.initial,
            "uid": uid,
        }
        if opts.form_class is not None:
            form_class = opts.form_class
            initial_fn = opts.initial

            def context_func(
                request: HttpRequest, **_kwargs: object
            ) -> types.SimpleNamespace:
                initial_data = initial_fn(request) if initial_fn else {}
                form_instance = form_class(initial=initial_data)
                return types.SimpleNamespace(form=form_instance)

            page._context_manager.register_context(
                fp,
                name,
                context_func,
                inherit_context=False,
            )

    def get_action_url(self, action_name: str) -> str:
        """URL for that action. KeyError if unknown."""
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
        """Single path that dispatches by uid."""
        if not self._registry:
            return []
        view = require_http_methods(["GET", "POST"])(self.dispatch)
        return [path("_next/form/<str:uid>/", view, name=URL_NAME_FORM_ACTION)]

    def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
        """Resolve uid to action and delegate to _FormActionDispatch.dispatch."""
        action_name = self._uid_to_name.get(uid)
        if action_name not in self._registry:
            return HttpResponseNotFound()
        meta = self._registry[action_name]
        return _FormActionDispatch.dispatch(
            self, request, action_name, cast("dict[str, Any]", meta)
        )

    def get_meta(self, action_name: str) -> dict[str, Any] | None:
        """Metadata for that action or None."""
        return cast("dict[str, Any] | None", self._registry.get(action_name))

    def render_form_fragment(
        self,
        request: HttpRequest,
        action_name: str,
        form: django_forms.Form | None,
        template_fragment: str | None = None,
    ) -> str:
        """Delegate to default (template or form.as_p)."""
        return _FormActionDispatch.render_form_fragment(
            self, request, action_name, form, template_fragment
        )


def _normalize_handler_response(
    raw: HttpResponse | str | None | object,
) -> HttpResponse | str | None:
    """Handlers may return None, str, HttpResponse, or object with .url. Normalize."""
    if raw is None or isinstance(raw, (HttpResponse, str)):
        return raw
    if hasattr(raw, "url") and (url := getattr(raw, "url", None)):
        return HttpResponseRedirect(url)
    return None


class _FormActionDispatch:
    """Dispatch and response normalization. Used by backends only."""

    @staticmethod
    def dispatch(
        backend: FormActionBackend,
        request: HttpRequest,
        action_name: str,
        meta: dict[str, Any],
    ) -> HttpResponse:
        """POST only. GET returns 405."""
        handler = meta["handler"]
        form_class = meta.get("form_class")

        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        if form_class is None:
            return _FormActionDispatch.ensure_http_response(
                _normalize_handler_response(handler(request)),
                request=request,
            )

        form = form_class(
            request.POST,
            request.FILES if hasattr(request, "FILES") else None,
        )
        if not form.is_valid():
            return _FormActionDispatch.form_response(
                backend, request, action_name, form, None
            )

        return _FormActionDispatch.ensure_http_response(
            _normalize_handler_response(handler(request, form)),
            request=request,
            action_name=action_name,
            backend=backend,
        )

    @staticmethod
    def form_response(
        backend: FormActionBackend,
        request: HttpRequest,
        action_name: str,
        form: django_forms.Form | None,
        template_fragment: str | None,
    ) -> HttpResponse:
        """HTML from backend.render_form_fragment wrapped in HttpResponse."""
        html = backend.render_form_fragment(
            request, action_name, form, template_fragment
        )
        return HttpResponse(html)

    @staticmethod
    def ensure_http_response(
        response: HttpResponse | str | None,
        request: HttpRequest | None = None,
        action_name: str | None = None,
        backend: FormActionBackend | None = None,
    ) -> HttpResponse:
        """Normalize handler response to HttpResponse."""
        response = _normalize_handler_response(response)

        if response is None:
            if request and action_name and backend:
                return _FormActionDispatch.form_response(
                    backend, request, action_name, None, None
                )
            return HttpResponse(status=204)
        if isinstance(response, HttpResponse):
            return response
        # str
        return HttpResponse(response)

    @staticmethod
    def render_form_fragment(
        backend: FormActionBackend,
        request: HttpRequest,
        action_name: str,
        form: django_forms.Form | None,
        template_fragment: str | None,  # noqa: ARG004
    ) -> str:
        """Render full page with form errors for regular POST response."""
        meta = backend.get_meta(action_name)
        if not meta:
            return form.as_p() if form else ""

        file_path = meta["file_path"]

        # Load full template with layout
        if file_path not in page._template_registry:
            page._load_template_for_file(file_path)
        template_str = page._template_registry.get(file_path)
        if not template_str:
            return form.as_p() if form else ""

        # Build context with form errors
        context_data = page._context_manager.collect_context(file_path, request)
        context_data["request"] = request
        if form is not None:
            context_data[action_name] = types.SimpleNamespace(form=form)

        return Template(template_str).render(Context(context_data))


class FormActionManager:
    """Aggregates backends. Yields URL patterns. Default backend is Registry."""

    def __init__(
        self,
        backends: list[FormActionBackend] | None = None,
    ) -> None:
        """One RegistryFormActionBackend if backends not given."""
        self._backends: list[FormActionBackend] = backends or [
            RegistryFormActionBackend(),
        ]

    def __repr__(self) -> str:
        """Repr with backend count."""
        return f"<{self.__class__.__name__} backends={len(self._backends)}>"

    def __iter__(self) -> Iterator[URLPattern]:
        """URL patterns from all backends."""
        for backend in self._backends:
            yield from backend.generate_urls()

    def register_action(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        options: FormActionOptions | None = None,
    ) -> None:
        """Forward to first backend."""
        self._backends[0].register_action(name, handler, options=options)

    def get_action_url(self, action_name: str) -> str:
        """URL from first backend that has this action. KeyError if none."""
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
    ) -> str:
        """Delegate to first backend."""
        return self._backends[0].render_form_fragment(
            request, action_name, form, template_fragment
        )

    @property
    def default_backend(self) -> FormActionBackend:
        """First backend."""
        return self._backends[0]


form_action_manager = FormActionManager()


def action(
    name: str,
    *,
    form_class: type[django_forms.Form] | None = None,
    initial: Callable[[HttpRequest], dict[str, Any]] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register form action handler."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        opts = FormActionOptions(
            form_class=form_class,
            initial=initial,
            file_path=_get_caller_path(2),
        )
        form_action_manager.register_action(name, func, options=opts)
        return func

    return decorator
