"""Base form classes and auto-registration machinery for next.forms."""

import re
import sys
from pathlib import Path
from typing import Any, Final

from django import forms as django_forms
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.forms.forms import BaseForm as DjangoBaseForm, DeclarativeFieldsMetaclass
from django.forms.models import BaseModelForm as DjangoBaseModelForm, ModelFormMetaclass
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404

from next.conf import next_framework_settings

from .manager import form_action_manager
from .uid import redirect_to_origin


_ANCHOR_FILE_NAMES: frozenset[str] = frozenset({"page.py", "component.py"})
_FRAMEWORK_ROOT: Final[Path] = Path(__file__).resolve().parent.parent


def _to_snake_case(name: str) -> str:
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s1).lower()


def _is_framework_file(file_path: str) -> bool:
    try:
        Path(file_path).resolve().relative_to(_FRAMEWORK_ROOT)
    except ValueError:
        return False
    else:
        return True


def _compute_scope(file_path: str) -> str:
    """Return 'page' if file_path names an anchor file, otherwise 'shared'."""
    custom_anchors = getattr(next_framework_settings, "FORM_ANCHOR_FILES", None)
    anchor_names = frozenset(custom_anchors or _ANCHOR_FILE_NAMES)
    return "page" if Path(file_path).resolve().name in anchor_names else "shared"


_outside_base_dir_classes: list[tuple[str, str]] = []
_invalid_meta_scope_classes: list[tuple[str, str]] = []
_instance_from_url_unknown_field: list[tuple[str, str, str]] = []
_instance_from_url_on_non_model_form: list[str] = []


def _record_invalid_meta_scope(cls: type, bad_value: object) -> None:
    """Append a (qualname, bad_scope) entry for the E047 system check."""
    _invalid_meta_scope_classes.append((cls.__qualname__, str(bad_value)))


def clear_auto_registration_state() -> None:
    """Clear accumulated registration warnings. For test isolation."""
    _outside_base_dir_classes.clear()
    _invalid_meta_scope_classes.clear()
    _instance_from_url_unknown_field.clear()
    _instance_from_url_on_non_model_form.clear()


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
    """Build a `Model.objects.get` lookup from the spec, or None when a kwarg is absent.

    A string spec names a URL kwarg that doubles as the model field. A dict
    spec maps `{url_kwarg_name: model_field_name}` for mismatched names.
    """
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
        _instance_from_url_on_non_model_form.append(cls.__qualname__)
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
            _instance_from_url_unknown_field.append(
                (cls.__qualname__, model._meta.label, db_field)
            )


_DJANGO_FORMS_SKIP_PARTS = frozenset({"widgets.py", "forms.py", "models.py"})


def _find_definition_frame() -> str:
    """Walk the call stack to find the file where a class was defined.

    Skips framework internals (metaclass __new__, __init_subclass__) to find
    the actual caller site.
    """
    depth = 1
    while True:
        try:
            frame = sys._getframe(depth)
        except ValueError:
            return ""
        filename = frame.f_code.co_filename
        p = Path(filename)
        # Skip Django forms internals (metaclass machinery)
        if p.parent.name == "forms" and p.name in _DJANGO_FORMS_SKIP_PARTS:
            depth += 1
            continue
        # Skip our own framework files
        try:
            p.resolve().relative_to(_FRAMEWORK_ROOT)
            depth += 1
            continue
        except ValueError:
            pass
        return filename


def _auto_register_form_class(cls: type) -> None:
    """Register a form subclass with form_action_manager."""
    file_path = _find_definition_frame()

    # Skip virtual frames (importlib bootstrap, interactive shell, etc.)
    if not file_path or file_path.startswith("<"):
        return

    if _is_framework_file(file_path):
        return

    base = getattr(settings, "BASE_DIR", None)
    if base is not None:
        try:
            Path(file_path).resolve().relative_to(Path(base).resolve())
        except ValueError:
            _outside_base_dir_classes.append((cls.__qualname__, file_path))
            return

    meta_scope = getattr(getattr(cls, "Meta", None), "scope", None)
    if meta_scope is not None and meta_scope not in ("page", "shared"):
        _invalid_meta_scope_classes.append((cls.__qualname__, str(meta_scope)))
        return

    scope = meta_scope if meta_scope is not None else _compute_scope(file_path)
    name = _to_snake_case(cls.__name__)
    form_action_manager.register_action(
        name,
        form_class=cls,
        file_path=str(Path(file_path).resolve()),
        scope=scope,
    )


class BaseForm(DjangoBaseForm):
    """Custom `BaseForm` extended with `get_initial` and `on_valid`."""

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Register subclass in form_action_manager automatically."""
        super().__init_subclass__(**kwargs)
        _auto_register_form_class(cls)
        _validate_instance_from_url(cls, is_model_form=False)

    @classmethod
    def get_initial(cls) -> dict[str, Any]:
        """Return initial data for this form."""
        return {}

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Handle a valid form submission."""
        return redirect_to_origin(request)


class BaseModelForm(DjangoBaseModelForm):
    """Custom `BaseModelForm` with `get_initial` and `on_valid` support."""

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Register subclass in form_action_manager automatically."""
        super().__init_subclass__(**kwargs)
        _auto_register_form_class(cls)
        _validate_instance_from_url(cls, is_model_form=True)

    @classmethod
    def get_initial(cls, **url_kwargs: object) -> object:
        """Return a model instance loaded from the URL, or an empty dict."""
        spec = getattr(getattr(cls, "Meta", None), "instance_from_url", None)
        if not spec:
            return {}
        lookup = _instance_lookup_from_spec(spec, url_kwargs)
        if lookup is None:
            return {}
        return get_object_or_404(cls._meta.model, **lookup)

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Save this model form and redirect to origin."""
        self.save()
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


__all__ = [
    "BaseForm",
    "BaseModelForm",
    "BooleanField",
    "CharField",
    "CheckboxInput",
    "ChoiceField",
    "DateField",
    "DateInput",
    "DateTimeField",
    "DateTimeInput",
    "DecimalField",
    "EmailField",
    "EmailInput",
    "FileField",
    "FloatField",
    "Form",
    "HiddenInput",
    "ImageField",
    "IntegerField",
    "ModelForm",
    "MultipleChoiceField",
    "NumberInput",
    "PasswordInput",
    "RegexField",
    "Select",
    "SelectMultiple",
    "TextInput",
    "Textarea",
    "TimeInput",
    "TypedChoiceField",
    "URLField",
    "URLInput",
    "ValidationError",
    "Widget",
]
