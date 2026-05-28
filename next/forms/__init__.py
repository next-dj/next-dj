"""Form actions and helpers for next-dj.

Register handlers with `@action`. Each action gets a stable UID
endpoint. Valid submissions run the handler. Invalid forms re-render
with errors. CSRF is applied for posted forms.

Advanced integrations can import dispatch helpers from ``next.forms.dispatch`` when a
submodule import clarifies intent.
"""

from next.pages import page

from . import checks, signals
from .backends import (
    ActionMeta,
    FormActionBackend,
    FormActionFactory,
    FormActionOptions,
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
    TimeInput,
    TypedChoiceField,
    URLField,
    URLInput,
    ValidationError,
    Widget,
    clear_auto_registration_state,
)
from .decorators import action
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


__all__ = [
    "FORM_ACTION_REVERSE_NAME",
    "URL_NAME_FORM_ACTION",
    "ActionMeta",
    "BaseForm",
    "BaseModelForm",
    "BooleanField",
    "CharField",
    "CheckboxInput",
    "ChoiceField",
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
    "action",
    "build_form_namespace_for_action",
    "checks",
    "cleanup_extra_initial",
    "field_spec",
    "form_action_manager",
    "form_spec",
    "formset_spec",
    "page",
    "redirect_to_origin",
    "signals",
    "validated_next_form_page_path",
]
