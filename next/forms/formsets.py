"""Helpers for working with Django formsets in custom UIs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import NON_FIELD_ERRORS
from django.forms import formsets as _django_formsets
from django.forms.formsets import DELETION_FIELD_NAME, BaseFormSet


if TYPE_CHECKING:
    from django.forms import BaseForm


def cleanup_extra_initial(formset: BaseFormSet) -> None:
    """Drop initial values on blank extra rows so untouched rows skip validation.

    Idempotent. Targets rows with `empty_permitted=True` and no instance pk.
    """
    for row_form in formset.forms:
        instance = getattr(row_form, "instance", None)
        if row_form.empty_permitted and not getattr(instance, "pk", None):
            row_form.initial = {}
            for field in row_form.fields.values():
                field.initial = None


def wire_errors(form: BaseForm | BaseFormSet) -> dict[str, list[str]]:
    """Return the wire-named errors of a form or a formset, its deleted rows skipped."""
    if not isinstance(form, BaseFormSet):
        return _messages(form, "")
    non_form = form.non_form_errors()
    errors: dict[str, list[str]] = {}
    if non_form:
        errors[NON_FIELD_ERRORS] = [str(m) for m in non_form]
    for row in form.forms:
        if not _deleted(form, row):
            errors.update(_messages(row, f"{row.prefix}-"))
    return errors


def error_message_count(form: BaseForm | BaseFormSet) -> int:
    """Count the error messages a form or a formset reports on the wire."""
    return sum(len(messages) for messages in wire_errors(form).values())


def error_field_names(form: BaseForm | BaseFormSet) -> tuple[str, ...]:
    """List the wire names of the failed fields, formset rows prefixed."""
    return tuple(wire_errors(form))


def _messages(form: BaseForm, prefix: str) -> dict[str, list[str]]:
    """Return the field errors of one form as strings, each name behind `prefix`."""
    return {
        f"{prefix}{name}": [str(m) for m in messages]
        for name, messages in form.errors.items()
    }


def _deleted(formset: BaseFormSet, row: BaseForm) -> bool:
    """Return True for a row marked DELETE, whose errors Django's `full_clean` skips."""
    if not formset.can_delete:
        return False
    return bool(getattr(row, "cleaned_data", {}).get(DELETION_FIELD_NAME, False))


_MISSING = object()


def __getattr__(name: str) -> object:
    """Resolve public `django.forms.formsets` names that next.dj does not override."""
    if not name.startswith("_"):
        value = getattr(_django_formsets, name, _MISSING)
        if value is not _MISSING:
            return value
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    """List the curated surface plus the public `django.forms.formsets` namespace."""
    django_public = {n for n in dir(_django_formsets) if not n.startswith("_")}
    return sorted(set(__all__) | django_public)


__all__ = ["cleanup_extra_initial"]
