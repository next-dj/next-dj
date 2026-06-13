"""Helpers for working with Django formsets in custom UIs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.forms import formsets as _django_formsets


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
