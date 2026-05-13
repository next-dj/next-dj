"""Form actions and helpers for next-dj.

Register handlers with `@action`. Each action gets a stable UID
endpoint. Valid submissions run the handler. Invalid forms re-render
with errors. CSRF is applied for posted forms.

Internal classes are reachable with deep imports of the form
`from next.forms.dispatch import FormActionDispatch`.
"""

from __future__ import annotations

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
)
from .decorators import action
from .dispatch import (
    FormActionDispatch,
    _bind_form_for_post,
    _filter_reserved_url_kwargs,
    _form_action_context_callable,
    _form_from_initial_data,
    _get_caller_path,
    _normalize_handler_response,
    _url_kwargs_from_post,
    _url_kwargs_from_resolver_or_post,
    build_form_namespace_for_action,
)
from .manager import FormActionManager, form_action_manager
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
    _make_uid,
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
    "_bind_form_for_post",
    "_filter_reserved_url_kwargs",
    "_form_action_context_callable",
    "_form_from_initial_data",
    "_get_caller_path",
    "_make_uid",
    "_normalize_handler_response",
    "_url_kwargs_from_post",
    "_url_kwargs_from_resolver_or_post",
    "action",
    "build_form_namespace_for_action",
    "checks",
    "field_spec",
    "form_action_manager",
    "form_spec",
    "formset_spec",
    "page",
    "signals",
    "validated_next_form_page_path",
]
