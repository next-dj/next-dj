"""Custom `BaseForm` and `BaseModelForm` plus Django form-class re-exports.

`BaseForm` and `BaseModelForm` extend Django with `get_initial`, which
lets `@action` resolve initial data from the request and URL kwargs
before binding the form. `Form` and `ModelForm` apply the right
metaclass so declarative field syntax continues to work. The Django
form classes re-exported below are a convenience so user code can do
`from next.forms import CharField, EmailField` without reaching into
Django directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django import forms as django_forms
from django.forms.forms import BaseForm as DjangoBaseForm, DeclarativeFieldsMetaclass
from django.forms.models import BaseModelForm as DjangoBaseModelForm, ModelFormMetaclass


if TYPE_CHECKING:
    from django.http import HttpRequest


class BaseForm(DjangoBaseForm):
    """Custom `BaseForm` that extends Django's `BaseForm` with `get_initial`."""

    @classmethod
    def get_initial(
        cls, _request: HttpRequest, *_args: object, **_kwargs: object
    ) -> dict[str, Any]:
        """Return initial data for this form.

        Override this method to provide initial data from the request. It is
        called automatically when creating form instances for GET requests.
        The returned dictionary becomes the `initial` parameter passed to the
        form constructor.
        """
        return {}


class BaseModelForm(DjangoBaseModelForm):
    """Custom `BaseModelForm` with `get_initial` support."""

    @classmethod
    def get_initial(
        cls, _request: HttpRequest, *_args: object, **_kwargs: object
    ) -> dict[str, Any] | object:
        """Return initial data or a model instance for this form.

        For `ModelForm` subclasses this may return either a dictionary, which
        becomes the `initial` parameter and results in a new instance on save,
        or a model instance, which becomes the `instance` parameter and
        updates the existing record.
        """
        return {}


class Form(BaseForm, metaclass=DeclarativeFieldsMetaclass):
    """A collection of fields with their associated data.

    This extends Django's `Form` with `get_initial` support.
    """


class ModelForm(BaseModelForm, metaclass=ModelFormMetaclass):
    """Form for editing a model instance.

    This extends Django's `ModelForm` with `get_initial` support.
    """


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
