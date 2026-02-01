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
from django.forms.forms import BaseForm as DjangoBaseForm, DeclarativeFieldsMetaclass
from django.forms.models import BaseModelForm as DjangoBaseModelForm, ModelFormMetaclass
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
            "uid": uid,
        }
        if opts.form_class is not None:
            form_class = opts.form_class

            def context_func(
                request: HttpRequest, *args: object, **kwargs: object
            ) -> types.SimpleNamespace:
                # Pass URL parameters (args, kwargs) to get_initial, same as context
                # functions form_class is guaranteed to have get_initial
                # from BaseForm/BaseModelForm
                if not hasattr(form_class, "get_initial"):
                    msg = f"Form class {form_class} must have get_initial method"
                    raise TypeError(msg)
                initial_data = form_class.get_initial(request, *args, **kwargs)
                # Check if initial_data is a model instance (for ModelForm)
                # Django models have _meta attribute
                has_meta = hasattr(initial_data, "_meta")
                is_model_instance = has_meta and hasattr(initial_data._meta, "model")
                if is_model_instance:
                    # It's a model instance, use it as instance parameter
                    # Only ModelForm supports instance parameter
                    if issubclass(form_class, BaseModelForm):
                        form_instance = form_class(instance=initial_data)
                    else:
                        msg = "instance parameter only supported for ModelForm"
                        raise TypeError(msg)
                else:
                    # It's a dict or other data, use as initial parameter
                    form_instance = form_class(initial=initial_data)  # type: ignore[assignment]
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
    def dispatch(  # noqa: C901, PLR0912
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

        # Extract URL parameters from hidden form fields (passed from file routing)
        url_kwargs: dict[str, object] = {}
        for key, value in request.POST.items():
            if key.startswith("_url_param_"):
                param_name = key.replace("_url_param_", "")
                # Try to convert to int if it looks like a number
                # POST values are always strings, but mypy sees str | list[object]
                if isinstance(value, str):
                    try:
                        url_kwargs[param_name] = int(value)
                    except ValueError:
                        url_kwargs[param_name] = value
                else:
                    url_kwargs[param_name] = value

        if form_class is None:
            return _FormActionDispatch.ensure_http_response(
                _normalize_handler_response(handler(request, **url_kwargs)),
                request=request,
            )

        # Get initial data or instance from form_class.get_initial
        # This allows ModelForm to receive instance for updating existing objects
        # form_class is guaranteed to have get_initial from BaseForm/BaseModelForm
        if not hasattr(form_class, "get_initial"):
            msg = f"Form class {form_class} must have get_initial method"
            raise TypeError(msg)
        initial_data = form_class.get_initial(request, **url_kwargs)

        # Check if initial_data is a model instance (for ModelForm)
        has_meta = hasattr(initial_data, "_meta")
        is_model_instance = has_meta and hasattr(initial_data._meta, "model")
        if is_model_instance:
            # It's a model instance, use it as instance parameter
            # Only ModelForm supports instance parameter
            if issubclass(form_class, BaseModelForm):
                form = form_class(
                    request.POST,
                    request.FILES if hasattr(request, "FILES") else None,
                    instance=initial_data,
                )
            else:
                msg = "instance parameter only supported for ModelForm"
                raise TypeError(msg)
        else:
            # It's a dict or other data, use as initial parameter
            form = form_class(
                request.POST,
                request.FILES if hasattr(request, "FILES") else None,
                initial=initial_data if initial_data else None,
            )
        if not form.is_valid():
            return _FormActionDispatch.form_response(
                backend, request, action_name, form, None
            )

        return _FormActionDispatch.ensure_http_response(
            _normalize_handler_response(handler(request, form, **url_kwargs)),
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

        # Extract URL parameters from POST data for context functions
        url_kwargs: dict[str, object] = {}
        if hasattr(request, "POST"):
            for key, value in request.POST.items():
                if key.startswith("_url_param_"):
                    param_name = key.replace("_url_param_", "")
                    # Try to convert to int if it looks like a number
                    # POST values are always strings, but mypy sees str | list[object]
                    if isinstance(value, str):
                        try:
                            url_kwargs[param_name] = int(value)
                        except ValueError:
                            url_kwargs[param_name] = value
                    else:
                        url_kwargs[param_name] = value

        # Build context with form errors
        # Pass URL kwargs to context functions, same as during normal page rendering
        context_data = page._context_manager.collect_context(
            file_path, request, **url_kwargs
        )
        context_data["request"] = request
        if form is not None:
            context_data[action_name] = types.SimpleNamespace(form=form)
            # Also add form as direct variable for template compatibility
            context_data["form"] = form

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
