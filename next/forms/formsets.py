"""Helpers for working with Django formsets in custom UIs."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from django.forms.formsets import BaseFormSet


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


__all__ = ["cleanup_extra_initial"]
