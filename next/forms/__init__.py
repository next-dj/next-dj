"""Form actions and helpers for next-dj.

Subclass `Form`, `ModelForm`, or `FormWizard` to auto-register an
action through `__init_subclass__`. Use `@action` for form-less
handlers. Each action gets a stable UID endpoint. Valid submissions
run the handler. Invalid forms re-render with errors. CSRF is applied
for posted forms.

Any public `django.forms` name resolves through `next.forms` unless
next.dj deliberately overrides it. The formset and modelform factories
plus `BoundField` are re-exported statically for type checkers, the
rest of the `django.forms` passthrough resolves at runtime only.
Framework machinery lives in the submodules, for example
`next.forms.dispatch` and `next.forms.manager`.
"""

from django import forms as _django_forms
from django.forms import (
    BoundField,
    formset_factory,
    inlineformset_factory,
    modelform_factory,
    modelformset_factory,
)

from . import signals
from .autodiscover import autodiscover_forms
from .backends import (
    ActionGuard,
    ActionRegistration,
    FormActionBackend,
    FormActionNotFoundError,
    RegistryBackendSnapshot,
    RegistryFormActionBackend,
)
from .base import (
    BaseForm,
    BaseModelForm,
    BooleanField,
    CharField,
    CheckboxInput,
    CheckboxSelectMultiple,
    ChoiceField,
    ClearableFileInput,
    DateField,
    DateInput,
    DateTimeField,
    DateTimeInput,
    DecimalField,
    DurationField,
    EmailField,
    EmailInput,
    FileField,
    FileInput,
    FloatField,
    Form,
    HiddenInput,
    ImageField,
    IntegerField,
    JSONField,
    ModelChoiceField,
    ModelForm,
    ModelMultipleChoiceField,
    MultipleChoiceField,
    NumberInput,
    PasswordInput,
    PermissionOutcome,
    RadioSelect,
    RegexField,
    Select,
    SelectMultiple,
    SlugField,
    Textarea,
    TextInput,
    TimeField,
    TimeInput,
    TypedChoiceField,
    URLField,
    URLInput,
    UUIDField,
    ValidationError,
    Widget,
)
from .decorators import action
from .dispatch import ActionOutcome, ActionOutcomeKind
from .formsets import cleanup_extra_initial
from .markers import DForm
from .origin import (
    OriginMatch,
    resolve_origin,
    resolve_url_to_match,
    resolve_url_to_page,
)
from .serializers import (
    FieldKind,
    FieldSpec,
    FormSectionSpec,
    FormsetRowSpec,
    FormsetSpec,
    FormSpec,
    field_spec,
    form_spec,
    formset_spec,
)
from .uid import redirect_to_origin
from .widgets import ComponentWidget
from .wizard import (
    CacheFormWizardBackend,
    FormWizard,
    FormWizardBackend,
    SessionFormWizardBackend,
)


_MISSING = object()


def __getattr__(name: str) -> object:
    """Resolve public `django.forms` names that next.dj does not override."""
    if not name.startswith("_"):
        value = getattr(_django_forms, name, _MISSING)
        if value is not _MISSING:
            return value
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    """List the curated surface plus the public `django.forms` namespace."""
    django_public = {n for n in dir(_django_forms) if not n.startswith("_")}
    return sorted(set(__all__) | django_public)


__all__ = [
    "ActionGuard",
    "ActionOutcome",
    "ActionOutcomeKind",
    "ActionRegistration",
    "BaseForm",
    "BaseModelForm",
    "BooleanField",
    "BoundField",
    "CacheFormWizardBackend",
    "CharField",
    "CheckboxInput",
    "CheckboxSelectMultiple",
    "ChoiceField",
    "ClearableFileInput",
    "ComponentWidget",
    "DForm",
    "DateField",
    "DateInput",
    "DateTimeField",
    "DateTimeInput",
    "DecimalField",
    "DurationField",
    "EmailField",
    "EmailInput",
    "FieldKind",
    "FieldSpec",
    "FileField",
    "FileInput",
    "FloatField",
    "Form",
    "FormActionBackend",
    "FormActionNotFoundError",
    "FormSectionSpec",
    "FormSpec",
    "FormWizard",
    "FormWizardBackend",
    "FormsetRowSpec",
    "FormsetSpec",
    "HiddenInput",
    "ImageField",
    "IntegerField",
    "JSONField",
    "ModelChoiceField",
    "ModelForm",
    "ModelMultipleChoiceField",
    "MultipleChoiceField",
    "NumberInput",
    "OriginMatch",
    "PasswordInput",
    "PermissionOutcome",
    "RadioSelect",
    "RegexField",
    "RegistryBackendSnapshot",
    "RegistryFormActionBackend",
    "Select",
    "SelectMultiple",
    "SessionFormWizardBackend",
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
    "action",
    "autodiscover_forms",
    "cleanup_extra_initial",
    "field_spec",
    "form_spec",
    "formset_factory",
    "formset_spec",
    "inlineformset_factory",
    "modelform_factory",
    "modelformset_factory",
    "redirect_to_origin",
    "resolve_origin",
    "resolve_url_to_match",
    "resolve_url_to_page",
    "signals",
]
