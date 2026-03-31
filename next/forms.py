"""Form actions and helpers for next-dj.

Register handlers with ``@forms.action``. Each action gets a stable UID endpoint.
Valid submissions run the handler. Invalid forms re-render with errors. CSRF is
applied for posted forms.
"""

import hashlib
import inspect
import types
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict, TypeVar, cast, get_args, get_origin

from django import forms as django_forms
from django.forms.forms import BaseForm as DjangoBaseForm, DeclarativeFieldsMetaclass
from django.forms.models import BaseModelForm as DjangoBaseModelForm, ModelFormMetaclass
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    HttpResponseNotFound,
    HttpResponseRedirect,
)
from django.template import Context as DjangoTemplateContext, Template
from django.urls import URLPattern, path, reverse
from django.urls.exceptions import NoReverseMatch
from django.views.decorators.http import require_http_methods

from .deps import DDependencyBase, RegisteredParameterProvider, resolver
from .pages import page
from .utils import caller_source_path


# Custom BaseForm and BaseModelForm with get_initial support
class BaseForm(DjangoBaseForm):
    """Custom BaseForm that extends Django's BaseForm with get_initial support."""

    @classmethod
    def get_initial(
        cls, _request: HttpRequest, *_args: object, **_kwargs: object
    ) -> dict[str, Any]:
        """Override this method to provide initial data from request.

        This method is called automatically when creating form instances
        for GET requests. Override it in subclasses to provide initial
        data based on the request and URL parameters.

        Returns a dictionary that will be used as the `initial` parameter
        when creating the form instance.
        """
        return {}


class BaseModelForm(DjangoBaseModelForm):
    """Custom BaseModelForm with get_initial support."""

    @classmethod
    def get_initial(
        cls, _request: HttpRequest, *_args: object, **_kwargs: object
    ) -> dict[str, Any] | object:
        """Override this method to provide initial data or instance from request.

        This method is called automatically when creating form instances
        for GET requests. Override it in subclasses to provide initial
        data based on the request and URL parameters.

        For ModelForm, you can return either:
        - A dictionary: will be used as the `initial` parameter
          (creates new instance on save)
        - A model instance: will be used as the `instance` parameter
          (updates existing instance on save)

        Returns a dictionary (for initial) or a model instance (for instance).
        """
        return {}


# Form and ModelForm classes with proper metaclasses
class Form(BaseForm, metaclass=DeclarativeFieldsMetaclass):
    """A collection of Fields, plus their associated data.

    This extends Django's Form with get_initial support.
    """


class ModelForm(BaseModelForm, metaclass=ModelFormMetaclass):
    """Form for editing a model instance.

    This extends Django's ModelForm with get_initial support.
    """


# D-marker for form DI (inherits from DDependencyBase)
_FormT = TypeVar("_FormT", bound=type)  # noqa: PYI018


class DForm[FormT](DDependencyBase[FormT]):
    r"""Annotation for injecting form instance by class.

    Use as DForm[MyForm] or DForm["MyForm"].
    """

    __slots__ = ()


class FormProvider(RegisteredParameterProvider):
    """``DForm[...]``, a concrete form class, or the parameter name ``form``."""

    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        """Context has a form matching the annotation or name."""
        form = getattr(context, "form", None)
        if form is None:
            return False
        if param.name == "form":
            return True
        ann = param.annotation
        if ann is inspect.Parameter.empty:
            return False
        origin = get_origin(ann)
        if origin is DForm:
            args = get_args(ann)
            if len(args) >= 1:
                form_class = args[0]
                if isinstance(form_class, type) and isinstance(form, form_class):
                    return True
            return False
        return isinstance(ann, type) and isinstance(form, ann)

    def resolve(self, _param: inspect.Parameter, context: object) -> object:
        """Return the form instance from context."""
        return getattr(context, "form", None)


# Re-export common Django form classes for convenience
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
ValidationError = django_forms.ValidationError
PasswordInput = django_forms.PasswordInput
TextInput = django_forms.TextInput
Textarea = django_forms.Textarea
Select = django_forms.Select
CheckboxInput = django_forms.CheckboxInput
SelectMultiple = django_forms.SelectMultiple
DateInput = django_forms.DateInput
DateTimeInput = django_forms.DateTimeInput
TimeInput = django_forms.TimeInput
NumberInput = django_forms.NumberInput
EmailInput = django_forms.EmailInput
URLInput = django_forms.URLInput
HiddenInput = django_forms.HiddenInput
Widget = django_forms.Widget


URL_NAME_FORM_ACTION = "form_action"

# When next.urls is included with app_name "next", reverse must use this.
FORM_ACTION_REVERSE_NAME = "next:form_action"


class ActionMeta(TypedDict, total=False):
    """Per-action data."""

    handler: Callable[..., Any]
    form_class: type[django_forms.Form] | None
    file_path: Path
    uid: str


@dataclass
class FormActionOptions:
    """Options for @action decorator."""

    form_class: type[django_forms.Form] | None = None
    file_path: Path | None = None


class FormActionBackend(ABC):
    """Storage and HTTP dispatch for ``@action`` handlers."""

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
        """Reverse URL for ``action_name``."""

    @abstractmethod
    def generate_urls(self) -> list[URLPattern]:
        """URLconf entries for this backend."""

    @abstractmethod
    def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
        """Run the handler for ``uid``."""

    def get_meta(self, action_name: str) -> dict[str, Any] | None:  # noqa: ARG002
        """Return optional per-action metadata for subclasses."""
        return None

    def render_form_fragment(
        self,
        request: HttpRequest,  # noqa: ARG002
        action_name: str,  # noqa: ARG002
        form: django_forms.Form | None,  # noqa: ARG002
        template_fragment: str | None = None,  # noqa: ARG002
    ) -> str:
        """Override this method to provide custom HTML for validation errors."""
        return ""


def _make_uid(file_path: Path, action_name: str) -> str:
    """Stable short id from file path and action name."""
    raw = f"{file_path!s}:{action_name}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _get_caller_path(back_count: int = 1) -> Path:
    """Path of the module that called into us. Skips frames from this file."""
    return caller_source_path(
        back_count=back_count,
        max_walk=15,
        skip_while_filename_endswith=("forms.py",),
    )


def _url_kwargs_from_post(request: HttpRequest) -> dict[str, object]:
    """Parse ``_url_param_*`` hidden fields from POST."""
    out: dict[str, object] = {}
    if not hasattr(request, "POST"):
        return out
    for key, value in request.POST.items():
        if not key.startswith("_url_param_"):
            continue
        param_name = key.replace("_url_param_", "")
        if isinstance(value, str):
            try:
                out[param_name] = int(value)
            except ValueError:
                out[param_name] = value
        else:
            out[param_name] = value
    return out


def _url_kwargs_from_resolver_or_post(request: HttpRequest) -> dict[str, object]:
    """URL kwargs from resolver match, else from POST hidden fields."""
    resolver_match = getattr(request, "resolver_match", None)
    if resolver_match and getattr(resolver_match, "kwargs", None):
        return dict(resolver_match.kwargs)
    if getattr(request, "method", None) == "POST" and hasattr(request, "POST"):
        return _url_kwargs_from_post(request)
    return {}


def _form_from_initial_data(
    form_class: type[django_forms.Form],
    initial_data: object,
) -> django_forms.Form:
    """Build an unbound form from ``get_initial`` result (dict or model instance)."""
    meta = getattr(initial_data, "_meta", None)
    is_model_instance = meta is not None and hasattr(meta, "model")
    if is_model_instance:
        if issubclass(form_class, BaseModelForm):
            return form_class(instance=initial_data)
        msg = "instance parameter only supported for ModelForm"
        raise TypeError(msg)
    return form_class(initial=cast("dict[str, Any] | None", initial_data))


def _form_action_context_callable(
    form_class: type[django_forms.Form],
) -> Callable[[HttpRequest], types.SimpleNamespace]:
    """Return a callable that builds a form instance for GET error rendering."""

    def context_func(request: HttpRequest) -> types.SimpleNamespace:
        if not hasattr(form_class, "get_initial"):
            msg = f"Form class {form_class} must have get_initial method"
            raise TypeError(msg)
        url_kwargs = _url_kwargs_from_resolver_or_post(request)
        dep_cache: dict[str, Any] = {}
        dep_stack: list[str] = []
        resolved = resolver.resolve_dependencies(
            form_class.get_initial,
            request=request,
            _cache=dep_cache,
            _stack=dep_stack,
            **url_kwargs,
        )
        initial_data = form_class.get_initial(**resolved)
        form_instance = _form_from_initial_data(form_class, initial_data)
        return types.SimpleNamespace(form=form_instance)

    return context_func


def _bind_form_for_post(
    form_class: type[django_forms.Form],
    request: HttpRequest,
    initial_data: object,
) -> django_forms.Form:
    """Bound form for POST validation using initial/instance from ``get_initial``."""
    files = request.FILES if hasattr(request, "FILES") else None
    meta = getattr(initial_data, "_meta", None)
    is_model_instance = meta is not None and hasattr(meta, "model")
    if is_model_instance:
        if issubclass(form_class, BaseModelForm):
            return form_class(
                request.POST,
                files,
                instance=initial_data,
            )
        msg = "instance parameter only supported for ModelForm"
        raise TypeError(msg)
    return form_class(
        request.POST,
        files,
        initial=cast("dict[str, Any] | None", initial_data),
    )


class RegistryFormActionBackend(FormActionBackend):
    """In-memory actions behind one dispatcher path keyed by UID."""

    def __init__(self) -> None:
        """Empty action map."""
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
            "uid": uid,
        }
        if opts.form_class is not None:
            page._context_manager.register_context(
                fp,
                name,
                _form_action_context_callable(opts.form_class),
                inherit_context=False,
            )

    def get_action_url(self, action_name: str) -> str:
        """Reverse URL for a registered name."""
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
        """One catch-all route when any action exists."""
        if not self._registry:
            return []
        view = require_http_methods(["GET", "POST"])(self.dispatch)
        return [path("_next/form/<str:uid>/", view, name=URL_NAME_FORM_ACTION)]

    def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
        """POST handler forwarded to ``_FormActionDispatch``."""
        action_name = self._uid_to_name.get(uid)
        if action_name not in self._registry:
            return HttpResponseNotFound()
        meta = self._registry[action_name]
        return _FormActionDispatch.dispatch(
            self, request, action_name, cast("dict[str, Any]", meta)
        )

    def get_meta(self, action_name: str) -> dict[str, Any] | None:
        """Return stored ``ActionMeta`` for the name, if any."""
        return cast("dict[str, Any] | None", self._registry.get(action_name))

    def render_form_fragment(
        self,
        request: HttpRequest,
        action_name: str,
        form: django_forms.Form | None,
        template_fragment: str | None = None,
    ) -> str:
        """Default fragment renderer."""
        return _FormActionDispatch.render_form_fragment(
            self, request, action_name, form, template_fragment
        )


def _normalize_handler_response(
    raw: HttpResponse | str | None | object,
) -> HttpResponse | str | None:
    """Coerce handler output to string, response, redirect, or ``None``."""
    if raw is None or isinstance(raw, (HttpResponse, str)):
        return raw
    if hasattr(raw, "url") and (url := getattr(raw, "url", None)):
        return HttpResponseRedirect(url)
    return None


class _FormActionDispatch:
    """Shared POST pipeline and response shaping for backends."""

    @staticmethod
    def dispatch(
        backend: FormActionBackend,
        request: HttpRequest,
        action_name: str,
        meta: dict[str, Any],
    ) -> HttpResponse:
        """Validate the form, run the handler, or re-render errors."""
        handler = meta["handler"]
        form_class = meta.get("form_class")

        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        url_kwargs = _url_kwargs_from_post(request)
        dep_cache: dict[str, Any] = {}
        dep_stack: list[str] = []

        if form_class is None:
            return _FormActionDispatch._dispatch_handler_only(
                handler,
                request,
                url_kwargs,
                dep_cache,
                dep_stack,
            )

        return _FormActionDispatch._dispatch_with_form(
            backend,
            request,
            action_name,
            handler,
            form_class,
            url_kwargs,
            dep_cache,
            dep_stack,
        )

    @staticmethod
    def _dispatch_handler_only(
        handler: Callable[..., Any],
        request: HttpRequest,
        url_kwargs: dict[str, object],
        dep_cache: dict[str, Any],
        dep_stack: list[str],
    ) -> HttpResponse:
        resolved = resolver.resolve_dependencies(
            handler,
            request=request,
            _cache=dep_cache,
            _stack=dep_stack,
            **url_kwargs,
        )
        return _FormActionDispatch.ensure_http_response(
            _normalize_handler_response(handler(**resolved)),
            request=request,
        )

    @staticmethod
    def _dispatch_with_form(  # noqa: PLR0913
        backend: FormActionBackend,
        request: HttpRequest,
        action_name: str,
        handler: Callable[..., Any],
        form_class: type[django_forms.Form],
        url_kwargs: dict[str, object],
        dep_cache: dict[str, Any],
        dep_stack: list[str],
    ) -> HttpResponse:
        if not hasattr(form_class, "get_initial"):
            msg = f"Form class {form_class} must have get_initial method"
            raise TypeError(msg)
        resolved = resolver.resolve_dependencies(
            form_class.get_initial,
            request=request,
            _cache=dep_cache,
            _stack=dep_stack,
            **url_kwargs,
        )
        initial_data = form_class.get_initial(**resolved)
        form = _bind_form_for_post(form_class, request, initial_data)
        if not form.is_valid():
            return _FormActionDispatch.form_response(
                backend, request, action_name, form, None
            )

        resolved = resolver.resolve_dependencies(
            handler,
            request=request,
            form=form,
            _cache=dep_cache,
            _stack=dep_stack,
            **url_kwargs,
        )
        return _FormActionDispatch.ensure_http_response(
            _normalize_handler_response(handler(**resolved)),
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
        """``HttpResponse`` around the error fragment."""
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
        """Coerce ``None``, str, or ``HttpResponse`` to a response."""
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
        """Full page template with bound form errors."""
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

        url_kwargs = _url_kwargs_from_post(request)

        # Build context with form errors
        # Pass URL kwargs to context functions, same as during normal page rendering
        context_data = page.build_render_context(file_path, request, **url_kwargs)
        if form is not None:
            context_data[action_name] = types.SimpleNamespace(form=form)
            # Also add form as direct variable for template compatibility
            context_data["form"] = form

        return Template(template_str).render(DjangoTemplateContext(context_data))


class FormActionManager:
    """Aggregates backends. Yields URL patterns. Default backend is Registry."""

    def __init__(
        self,
        backends: list[FormActionBackend] | None = None,
    ) -> None:
        """Initialize with backends or a single ``RegistryFormActionBackend``."""
        self._backends: list[FormActionBackend] = backends or [
            RegistryFormActionBackend(),
        ]

    def __repr__(self) -> str:
        """Debug representation."""
        return f"<{self.__class__.__name__} backends={len(self._backends)}>"

    def __iter__(self) -> Iterator[URLPattern]:
        """Concatenated patterns from each backend."""
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
        """Reverse URL from the first backend that knows ``action_name``."""
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
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register form action handler."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        opts = FormActionOptions(
            form_class=form_class,
            file_path=_get_caller_path(2),
        )
        form_action_manager.register_action(name, func, options=opts)
        return func

    return decorator
