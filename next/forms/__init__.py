"""Form actions and helpers for next-dj.

Register handlers with `@action`. Each action gets a stable UID
endpoint. Valid submissions run the handler. Invalid forms re-render
with errors. CSRF is applied for posted forms.

Advanced integrations can import dispatch helpers from ``next.forms.dispatch`` when a
submodule import clarifies intent.
"""

from next.pages import page

from . import checks, signals
from .autodiscover import autodiscover_forms, clear_discovered
from .backends import (
    ActionMeta,
    ActionRegistration,
    FormActionBackend,
    FormActionFactory,
    RegistryFormActionBackend,
)
from .base import (
    BaseForm,
    BaseModelForm,
    BooleanField,
    CharField,
    CheckboxInput,
    ChoiceField,
    DateField,
    DateInput,
    DateTimeField,
    DateTimeInput,
    DecimalField,
    EmailField,
    EmailInput,
    FileField,
    FloatField,
    Form,
    HiddenInput,
    ImageField,
    IntegerField,
    ModelForm,
    MultipleChoiceField,
    NumberInput,
    PasswordInput,
    RegexField,
    Select,
    SelectMultiple,
    Textarea,
    TextInput,
    TimeField,
    TimeInput,
    TypedChoiceField,
    URLField,
    URLInput,
    ValidationError,
    Widget,
)
from .decorators import action
from .dispatch import ActionOutcome, ActionOutcomeKind, FormActionDispatch
from .exceptions import FormActionNotFound
from .formsets import cleanup_extra_initial
from .manager import (
    FormActionManager,
    build_form_namespace_for_action,
    form_action_manager,
)
from .markers import DForm, FormProvider
from .registration import registration_diagnostics
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
from .uid import (
    FORM_ACTION_REVERSE_NAME,
    URL_NAME_FORM_ACTION,
    redirect_to_origin,
)
from .widgets import ComponentWidget
from .wizard import (
    CacheFormWizardBackend,
    FormWizard,
    FormWizardBackend,
    SessionFormWizardBackend,
    WizardBackendManager,
    wizard_backend_manager,
)


def reset_form_registration_state() -> None:
    """Clear every form registry and registration-warning buffer for test isolation."""
    form_action_manager.clear_registries()
    registration_diagnostics.clear()
    clear_discovered()
    wizard_backend_manager.reset()


__all__ = [
    "FORM_ACTION_REVERSE_NAME",
    "URL_NAME_FORM_ACTION",
    "ActionMeta",
    "ActionOutcome",
    "ActionOutcomeKind",
    "ActionRegistration",
    "BaseForm",
    "BaseModelForm",
    "BooleanField",
    "CacheFormWizardBackend",
    "CharField",
    "CheckboxInput",
    "ChoiceField",
    "ComponentWidget",
    "DForm",
    "DateField",
    "DateInput",
    "DateTimeField",
    "DateTimeInput",
    "DecimalField",
    "EmailField",
    "EmailInput",
    "FieldKind",
    "FieldSpec",
    "FileField",
    "FloatField",
    "Form",
    "FormActionBackend",
    "FormActionDispatch",
    "FormActionFactory",
    "FormActionManager",
    "FormActionNotFound",
    "FormProvider",
    "FormSectionSpec",
    "FormSpec",
    "FormWizard",
    "FormWizardBackend",
    "FormsetRowSpec",
    "FormsetSpec",
    "HiddenInput",
    "ImageField",
    "IntegerField",
    "ModelForm",
    "MultipleChoiceField",
    "NumberInput",
    "PasswordInput",
    "RegexField",
    "RegistryFormActionBackend",
    "Select",
    "SelectMultiple",
    "SessionFormWizardBackend",
    "TextInput",
    "Textarea",
    "TimeField",
    "TimeInput",
    "TypedChoiceField",
    "URLField",
    "URLInput",
    "ValidationError",
    "Widget",
    "WizardBackendManager",
    "action",
    "autodiscover_forms",
    "build_form_namespace_for_action",
    "checks",
    "cleanup_extra_initial",
    "field_spec",
    "form_action_manager",
    "form_spec",
    "formset_spec",
    "page",
    "redirect_to_origin",
    "reset_form_registration_state",
    "signals",
    "wizard_backend_manager",
]
