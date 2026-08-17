"""Base form classes and auto-registration machinery for next.forms."""

import inspect
import os
import re
import sys
from pathlib import Path
from typing import Any, Final, override

from django import forms as django_forms
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Model
from django.forms.forms import BaseForm as DjangoBaseForm, DeclarativeFieldsMetaclass
from django.forms.models import BaseModelForm as DjangoBaseModelForm, ModelFormMetaclass
from django.forms.renderers import DjangoTemplates
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404

from next.conf import next_framework_settings
from next.utils import defining_file

from .backends import (
    ActionGuard,
    ActionRegistration,
    _resolved_path_str,
    build_action_guard,
)
from .diagnostics import registration_diagnostics
from .manager import form_action_manager
from .uid import redirect_to_origin


def _root_prefix(root: str) -> str:
    """Return the root with one trailing separator so prefix tests respect segments."""
    return root if root.endswith(os.sep) else root + os.sep


_ANCHOR_FILE_NAMES: frozenset[str] = frozenset({"page.py", "component.py"})
_SELF_REGISTERED_ATTR: Final[str] = "__next_registered__"
_FRAMEWORK_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_DJANGO_FORMS_ROOT: Final[Path] = Path(inspect.getfile(django_forms)).resolve().parent
_FRAMEWORK_ROOT_STR: Final[str] = str(_FRAMEWORK_ROOT)
_FRAMEWORK_ROOT_PREFIX: Final[str] = _root_prefix(_FRAMEWORK_ROOT_STR)
# A class attributed to either root was built by type() inside foreign code,
# so only the stack names the file that asked for it.
_FOREIGN_ROOTS: Final[tuple[tuple[str, str], ...]] = (
    (str(_DJANGO_FORMS_ROOT), _root_prefix(str(_DJANGO_FORMS_ROOT))),
    (_FRAMEWORK_ROOT_STR, _FRAMEWORK_ROOT_PREFIX),
)

# Both roots are process constants, so a per-path answer can never go stale.
# The BASE_DIR decision stays uncached because settings repoint it.
_foreign_file_cache: dict[str, bool] = {}

# HttpResponseRedirect is a subclass of HttpResponse, so the login-redirect
# short-circuit needs no extra arm.
type PermissionOutcome = bool | HttpResponse | None


_ACRONYM_BOUNDARY_RE: Final[re.Pattern[str]] = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CASE_BOUNDARY_RE: Final[re.Pattern[str]] = re.compile(r"([a-z\d])([A-Z])")


def _to_snake_case(name: str) -> str:
    s1 = _ACRONYM_BOUNDARY_RE.sub(r"\1_\2", name)
    return _CASE_BOUNDARY_RE.sub(r"\1_\2", s1).lower()


def _is_foreign_file(file_path: str) -> bool:
    """Return True when file_path resolves inside django.forms or the framework."""
    cached = _foreign_file_cache.get(file_path)
    if cached is None:
        resolved = _resolved_path_str(file_path)
        cached = any(
            resolved == root or resolved.startswith(prefix)
            for root, prefix in _FOREIGN_ROOTS
        )
        _foreign_file_cache[file_path] = cached
    return cached


def _compute_scope(file_path: str) -> str:
    """Return 'page' if file_path names an anchor file, otherwise 'shared'."""
    configured = next_framework_settings.FORM_ANCHOR_FILES
    anchor_names = (
        frozenset(configured) if configured is not None else _ANCHOR_FILE_NAMES
    )
    # The tail after the last separator equals PurePath.name for a resolved path.
    name = _resolved_path_str(file_path).rpartition(os.sep)[2]
    return "page" if name in anchor_names else "shared"


def _record_invalid_meta_scope(cls: type, bad_value: object) -> None:
    """Append a (qualname, bad_scope) entry for the E047 system check."""
    registration_diagnostics.invalid_meta_scope.append(
        (cls.__qualname__, str(bad_value))
    )


def _instance_from_url_db_fields(spec: object) -> list[str]:
    """Return the model lookup field names named by an instance_from_url spec."""
    if isinstance(spec, str):
        return [spec]
    if isinstance(spec, dict):
        return [str(v) for v in spec.values()]
    return []


def _instance_lookup_from_spec(
    spec: object, url_kwargs: dict[str, object]
) -> dict[str, object] | None:
    """Build a `Model.objects.get` lookup from the spec, or None on a missing kwarg."""
    if isinstance(spec, str):
        value = url_kwargs.get(spec)
        if value is None:
            return None
        return {spec: value}
    if isinstance(spec, dict):
        lookup: dict[str, object] = {}
        for url_kwarg_name, db_field in spec.items():
            value = url_kwargs.get(url_kwarg_name)
            if value is None:
                return None
            lookup[str(db_field)] = value
        return lookup
    return None


def _validate_instance_from_url(cls: type, *, is_model_form: bool) -> None:
    """Record E048/E049 problems for a class that declares Meta.instance_from_url."""
    meta = getattr(cls, "Meta", None)
    spec = getattr(meta, "instance_from_url", None)
    if not spec:
        return
    if not is_model_form:
        registration_diagnostics.instance_from_url_on_non_model_form.append(
            cls.__qualname__
        )
        return
    model = getattr(meta, "model", None)
    if model is None:
        return
    for db_field in _instance_from_url_db_fields(spec):
        if db_field == "pk":
            continue
        try:
            model._meta.get_field(db_field.split("__")[0])
        except FieldDoesNotExist:
            registration_diagnostics.instance_from_url_unknown_field.append(
                (cls.__qualname__, model._meta.label, db_field)
            )


def _find_frame_outside() -> str:
    """Walk the stack for the first frame outside django.forms and the framework."""
    depth = 1
    while True:
        try:
            frame = sys._getframe(depth)
        except ValueError:
            return ""
        filename = frame.f_code.co_filename
        if _is_foreign_file(filename):
            depth += 1
            continue
        return filename


def _module_declared_file(cls: type) -> str | None:
    """Return the file of the module that still binds cls under its own name.

    The file router execs every `page.py` under one throwaway module name it
    never registers, so a same-named module a project happens to import must
    not answer for a class it never declared.
    """
    module = sys.modules.get(getattr(cls, "__module__", "") or "")
    if module is None or getattr(module, cls.__name__, None) is not cls:
        return None
    try:
        return str(defining_file(cls))
    except (OSError, TypeError):
        # A module without __file__, such as one built for an interactive
        # session, names no file for the classes declared in it.
        return None


def _definition_file_of(cls: type) -> str:
    """Return the file where cls was declared, empty when no frame names one.

    `__init_subclass__` runs while the declaring frame is still on the stack,
    so the walk answers wherever the module cannot. A foreign file never
    survives it, which is why the caller needs no framework arm.
    """
    file_path = _module_declared_file(cls)
    if file_path is None or _is_foreign_file(file_path):
        return _find_frame_outside()
    return file_path


def _registration_gate(cls: type) -> tuple[str, str, str] | None:
    """Run the shared registration policy, returning (scope, name, file_path)."""
    # Like Django model Meta, abstract is never inherited, so only the class's
    # own namespace opts it out of registration.
    if getattr(cls.__dict__.get("Meta"), "abstract", False):
        return None

    file_path = _definition_file_of(cls)

    # Skip virtual frames (importlib bootstrap, interactive shell, etc.)
    if not file_path or file_path.startswith("<"):
        return None

    base = getattr(settings, "BASE_DIR", None)
    if base is not None:
        resolved = _resolved_path_str(file_path)
        base_root = _resolved_path_str(str(base))
        if resolved != base_root and not resolved.startswith(_root_prefix(base_root)):
            registration_diagnostics.outside_base_dir.append(
                (cls.__qualname__, file_path)
            )
            return None

    meta_scope = getattr(getattr(cls, "Meta", None), "scope", None)
    if meta_scope is not None and meta_scope not in ("page", "shared"):
        _record_invalid_meta_scope(cls, meta_scope)
        return None

    scope = meta_scope if meta_scope is not None else _compute_scope(file_path)
    return scope, _to_snake_case(cls.__name__), _resolved_path_str(file_path)


def _meta_guard(cls: type) -> ActionGuard | None:
    """Build the access guard declared by Meta, inherited unlike Meta.abstract."""
    meta = getattr(cls, "Meta", None)
    return build_action_guard(
        login_required=bool(getattr(meta, "login_required", False)),
        permission_required=getattr(meta, "permission_required", None),
    )


def _declared_success_url(cls: type) -> str | None:
    """Return the evaluated Meta.success_url, or None when undeclared."""
    value = getattr(getattr(cls, "Meta", None), "success_url", None)
    if value is None:
        return None
    if callable(value):
        value = value()
    return str(value)


def _format_success_message(cls: type, cleaned_data: dict[str, Any]) -> str:
    """Interpolate Meta.success_message over cleaned_data, empty when undeclared."""
    template = getattr(getattr(cls, "Meta", None), "success_message", "")
    if not template:
        return ""
    return str(template) % cleaned_data


def _is_self_registered(cls: type) -> bool:
    """Return True when auto-registration stamped this exact class."""
    # The lookup is own-dict on purpose, a concrete subclass of a registered
    # base must not inherit the marker.
    return _SELF_REGISTERED_ATTR in cls.__dict__


def _auto_register_form_class(cls: type) -> None:
    """Register a form subclass with form_action_manager."""
    gate = _registration_gate(cls)
    if gate is None:
        return
    setattr(cls, _SELF_REGISTERED_ATTR, True)
    scope, name, file_path = gate
    form_action_manager.register_action(
        ActionRegistration(
            name=name,
            file_path=file_path,
            scope=scope,
            form_class=cls,
            guard=_meta_guard(cls),
        )
    )


class _DivFormRenderer(DjangoTemplates):
    """Renderer pinning the div template so `{{ form }}` ignores FORM_RENDERER."""

    # Pinning the div template keeps bare `{{ form }}` output stable regardless
    # of the project's FORM_RENDERER setting.
    form_template_name = "django/forms/div.html"


_div_form_renderer = _DivFormRenderer()


def _hook_func(method: object) -> object:
    """Return a classmethod's underlying function, or the method itself."""
    return getattr(method, "__func__", method)


def _stamp_hook_flag(base_hook: object, override_hook: object) -> bool:
    """Return True when a subclass overrides the base hook, by __func__ identity."""
    return _hook_func(override_hook) is not _hook_func(base_hook)


class _PermissionHooks:
    """Opt-in DI-resolved permission gates layered over the static ActionGuard.

    `__init_subclass__` stamps a presence flag per hook so an undeclared hook
    costs the dispatcher no resolver call.
    """

    _has_check_permissions: bool = False
    _has_object_permission: bool = False

    @override
    def __init_subclass__(cls, **kwargs) -> None:
        """Stamp the per-subclass hook-presence flags via __func__ identity."""
        super().__init_subclass__(**kwargs)
        cls._has_check_permissions = _stamp_hook_flag(
            _PermissionHooks.check_permissions, cls.check_permissions
        )
        cls._has_object_permission = _stamp_hook_flag(
            _PermissionHooks.has_object_permission, cls.has_object_permission
        )

    @classmethod
    def check_permissions(cls) -> PermissionOutcome:
        """View-level gate, DI-resolved like get_initial. None or True allows."""
        return None

    def has_object_permission(self) -> PermissionOutcome:
        """Object-level gate after binding, DI-resolved. None or True allows."""
        return None


class BaseForm(_PermissionHooks, DjangoBaseForm):
    """Custom `BaseForm` extended with `get_initial` and `on_valid`."""

    default_renderer = _div_form_renderer

    @override
    def __init_subclass__(cls, **kwargs) -> None:
        """Register subclass in form_action_manager automatically."""
        super().__init_subclass__(**kwargs)
        _auto_register_form_class(cls)
        _validate_instance_from_url(cls, is_model_form=False)

    @classmethod
    def get_initial(cls) -> dict[str, Any]:
        """Return initial data for this form."""
        return {}

    def get_success_message(self, cleaned_data: dict[str, Any]) -> str:
        """Return the flash message for a valid submission, empty string for none."""
        return _format_success_message(type(self), cleaned_data)

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Redirect to Meta.success_url when declared, otherwise back to origin."""
        url = _declared_success_url(type(self))
        if url is not None:
            return HttpResponseRedirect(url)
        return redirect_to_origin(request)


class BaseModelForm(_PermissionHooks, DjangoBaseModelForm):
    """Custom `BaseModelForm` with `get_initial` and `on_valid` support."""

    default_renderer = _div_form_renderer

    @override
    def __init_subclass__(cls, **kwargs) -> None:
        """Register subclass in form_action_manager automatically."""
        super().__init_subclass__(**kwargs)
        _auto_register_form_class(cls)
        _validate_instance_from_url(cls, is_model_form=True)

    @classmethod
    def get_initial(cls, **url_kwargs) -> dict[str, Any] | Model:
        """Return a model instance loaded from the URL, or an empty dict."""
        spec = getattr(getattr(cls, "Meta", None), "instance_from_url", None)
        if not spec:
            return {}
        lookup = _instance_lookup_from_spec(spec, url_kwargs)
        if lookup is None:
            return {}
        model: type[Model] = cls._meta.model
        return get_object_or_404(model, **lookup)

    def get_success_message(self, cleaned_data: dict[str, Any]) -> str:
        """Return the flash message for a valid submission, empty string for none."""
        return _format_success_message(type(self), cleaned_data)

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Save this model form, then follow Meta.success_url or the origin."""
        self.save()
        url = _declared_success_url(type(self))
        if url is not None:
            return HttpResponseRedirect(url)
        return redirect_to_origin(request)


class Form(BaseForm, metaclass=DeclarativeFieldsMetaclass):
    """A collection of fields with `get_initial` and `on_valid` support."""


class ModelForm(BaseModelForm, metaclass=ModelFormMetaclass):
    """Form for editing a model instance with `get_initial` and `on_valid` support."""


CharField = django_forms.CharField
EmailField = django_forms.EmailField
IntegerField = django_forms.IntegerField
BooleanField = django_forms.BooleanField
ChoiceField = django_forms.ChoiceField
TypedChoiceField = django_forms.TypedChoiceField
MultipleChoiceField = django_forms.MultipleChoiceField
ModelChoiceField = django_forms.ModelChoiceField
ModelMultipleChoiceField = django_forms.ModelMultipleChoiceField
DateField = django_forms.DateField
DateTimeField = django_forms.DateTimeField
TimeField = django_forms.TimeField
DurationField = django_forms.DurationField
DecimalField = django_forms.DecimalField
FloatField = django_forms.FloatField
URLField = django_forms.URLField
SlugField = django_forms.SlugField
UUIDField = django_forms.UUIDField
JSONField = django_forms.JSONField
RegexField = django_forms.RegexField
FileField = django_forms.FileField
ImageField = django_forms.ImageField
ValidationError = django_forms.ValidationError
PasswordInput = django_forms.PasswordInput
TextInput = django_forms.TextInput
Textarea = django_forms.Textarea
Select = django_forms.Select
RadioSelect = django_forms.RadioSelect
CheckboxInput = django_forms.CheckboxInput
CheckboxSelectMultiple = django_forms.CheckboxSelectMultiple
SelectMultiple = django_forms.SelectMultiple
DateInput = django_forms.DateInput
DateTimeInput = django_forms.DateTimeInput
TimeInput = django_forms.TimeInput
NumberInput = django_forms.NumberInput
EmailInput = django_forms.EmailInput
URLInput = django_forms.URLInput
HiddenInput = django_forms.HiddenInput
FileInput = django_forms.FileInput
ClearableFileInput = django_forms.ClearableFileInput
Widget = django_forms.Widget


__all__ = [
    "BaseForm",
    "BaseModelForm",
    "BooleanField",
    "CharField",
    "CheckboxInput",
    "CheckboxSelectMultiple",
    "ChoiceField",
    "ClearableFileInput",
    "DateField",
    "DateInput",
    "DateTimeField",
    "DateTimeInput",
    "DecimalField",
    "DurationField",
    "EmailField",
    "EmailInput",
    "FileField",
    "FileInput",
    "FloatField",
    "Form",
    "HiddenInput",
    "ImageField",
    "IntegerField",
    "JSONField",
    "ModelChoiceField",
    "ModelForm",
    "ModelMultipleChoiceField",
    "MultipleChoiceField",
    "NumberInput",
    "PasswordInput",
    "PermissionOutcome",
    "RadioSelect",
    "RegexField",
    "Select",
    "SelectMultiple",
    "SlugField",
    "TextInput",
    "Textarea",
    "TimeField",
    "TimeInput",
    "TypedChoiceField",
    "URLField",
    "URLInput",
    "UUIDField",
    "ValidationError",
    "Widget",
]
