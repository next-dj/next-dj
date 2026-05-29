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
    FormActionBackend,
    FormActionFactory,
    FormActionOptions,
    RegistryFormActionBackend,
    clear_action_collisions,
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
    TimeInput,
    TypedChoiceField,
    URLField,
    URLInput,
    ValidationError,
    Widget,
    clear_auto_registration_state,
)
from .decorators import action, clear_action_applied_to_class
from .dispatch import FormActionDispatch
from .formsets import cleanup_extra_initial
from .manager import (
    FormActionManager,
    build_form_namespace_for_action,
    form_action_manager,
)
from .markers import DForm, FormProvider
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
    validated_next_form_page_path,
)
from .widgets import ComponentWidget
from .wizard import (
    CacheFormWizardBackend,
    FormWizard,
    FormWizardBackend,
    WizardBackendManager,
    clear_wizard_registration_state,
    wizard_backend_manager,
)


def reset_form_registration_state() -> None:
    """Clear every form registry and registration-warning buffer.

    Resets the action registry, the auto-registration warning buffers, the
    collision tracker, the @action-on-class tracker, the autodiscover guard,
    the wizard registry, and the cached wizard backend. Intended for test
    isolation and manual hot-reload flows.
    """
    form_action_manager.clear_registries()
    clear_auto_registration_state()
    clear_action_collisions()
    clear_action_applied_to_class()
    clear_discovered()
    clear_wizard_registration_state()
    wizard_backend_manager.reset()


__all__ = [
    "FORM_ACTION_REVERSE_NAME",
    "URL_NAME_FORM_ACTION",
    "ActionMeta",
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
    "FormActionOptions",
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
    "TextInput",
    "Textarea",
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
    "validated_next_form_page_path",
    "wizard_backend_manager",
]
