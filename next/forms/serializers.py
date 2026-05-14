"""Frozen-dataclass specs for rendering Django forms in custom templates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from django import forms as django_forms
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper


if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from django.forms import BaseForm, BoundField
    from django.forms.formsets import BaseFormSet


FieldKind = Literal["textarea", "checkbox", "select", "select_multi", "input"]


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Render-time descriptor for one `BoundField`."""

    bound: BoundField
    kind: FieldKind
    input_type: str
    value: Any
    selected: tuple[str, ...]
    is_extra: bool


@dataclass(frozen=True, slots=True)
class FormsetRowSpec:
    """One row inside a `FormsetSpec`. Render `hidden_html` with `|safe`."""

    fields: tuple[FieldSpec, ...]
    hidden_html: str
    delete_field: BoundField | None
    errors: Mapping[str, list[str]]
    is_extra: bool


@dataclass(frozen=True, slots=True)
class FormsetSpec:
    """Template-friendly view of a Django formset (inline or standalone)."""

    prefix: str
    verbose_name_plural: str
    management_form: BaseForm
    rows: tuple[FormsetRowSpec, ...]
    non_form_errors: tuple[str, ...]
    can_delete: bool


@dataclass(frozen=True, slots=True)
class FormSectionSpec:
    """One labelled section in a `FormSpec` (matches a Django admin fieldset)."""

    label: str
    description: str
    fields: tuple[FieldSpec, ...]


@dataclass(frozen=True, slots=True)
class FormSpec:
    """Top-level spec for rendering a form with optional fieldsets."""

    sections: tuple[FormSectionSpec, ...]
    non_field_errors: tuple[str, ...]


def field_spec(bound: BoundField, *, is_extra: bool = False) -> FieldSpec:
    """Classify a `BoundField` into a `FieldSpec`."""
    widget = bound.field.widget
    if isinstance(widget, RelatedFieldWidgetWrapper):
        widget = widget.widget
    if isinstance(widget, django_forms.Textarea):
        kind: FieldKind = "textarea"
        input_type = ""
    elif isinstance(widget, django_forms.CheckboxInput):
        kind, input_type = "checkbox", ""
    elif isinstance(widget, django_forms.SelectMultiple):
        kind, input_type = "select_multi", ""
    elif isinstance(widget, django_forms.Select):
        kind, input_type = "select", ""
    else:
        kind = "input"
        input_type = getattr(widget, "input_type", "text")

    value = bound.value()
    selected: tuple[str, ...] = ()
    if kind == "select_multi":
        raw = value if value is not None else []
        if not isinstance(raw, (list, tuple)):
            raw = [raw]
        selected = tuple(str(v) for v in raw)
    return FieldSpec(
        bound=bound,
        kind=kind,
        input_type=input_type,
        value=value,
        selected=selected,
        is_extra=is_extra,
    )


def formset_spec(formset: BaseFormSet) -> FormsetSpec:
    """Build a `FormsetSpec` from a Django formset."""
    rows: list[FormsetRowSpec] = []
    for row_form in formset.forms:
        # Plain `BaseFormSet` rows do not expose `.instance`; treat absent
        # instance as "no pk", so `empty_permitted` alone marks the row blank.
        instance = getattr(row_form, "instance", None)
        is_extra = bool(row_form.empty_permitted and not getattr(instance, "pk", None))
        visible_names = [
            name
            for name in row_form.fields
            if name != "DELETE" and not row_form[name].is_hidden
        ]
        hidden_html = "".join(
            str(row_form[name]) for name in row_form.fields if row_form[name].is_hidden
        )
        delete_field = row_form["DELETE"] if "DELETE" in row_form.fields else None
        rows.append(
            FormsetRowSpec(
                fields=tuple(
                    field_spec(row_form[name], is_extra=is_extra)
                    for name in visible_names
                ),
                hidden_html=hidden_html,
                delete_field=delete_field,
                errors={name: list(errs) for name, errs in row_form.errors.items()},
                is_extra=is_extra,
            )
        )

    model = getattr(formset, "model", None)
    verbose_name_plural = (
        str(model._meta.verbose_name_plural)
        if model is not None and hasattr(model, "_meta")
        else ""
    )
    return FormsetSpec(
        prefix=formset.prefix or "",
        verbose_name_plural=verbose_name_plural,
        management_form=formset.management_form,
        rows=tuple(rows),
        non_form_errors=tuple(str(e) for e in formset.non_form_errors()),
        can_delete=bool(getattr(formset, "can_delete", False)),
    )


def _flatten_fieldset_names(
    fieldsets: Iterable[tuple[str | None, Mapping[str, Any]]],
) -> set[str]:
    """Flat set of field names referenced by `fieldsets` (nested tuples allowed)."""
    out: set[str] = set()
    for _, opts in fieldsets:
        for entry in opts.get("fields", ()):
            if isinstance(entry, (list, tuple)):
                out.update(entry)
            else:
                out.add(entry)
    return out


def form_spec(
    form: BaseForm,
    fieldsets: Sequence[tuple[str | None, Mapping[str, Any]]] | None = None,
) -> FormSpec:
    """Group `form`'s fields into sections per Django admin `(label, opts)`."""
    sections: tuple[FormSectionSpec, ...]
    if fieldsets is None:
        all_fields = tuple(field_spec(form[name]) for name in form.fields)
        sections = (FormSectionSpec(label="", description="", fields=all_fields),)
    else:
        rendered: set[str] = set()
        built: list[FormSectionSpec] = []
        for label, opts in fieldsets:
            specs: list[FieldSpec] = []
            for entry in opts.get("fields", ()):
                names = entry if isinstance(entry, (list, tuple)) else (entry,)
                for name in names:
                    if name in form.fields:
                        specs.append(field_spec(form[name]))
                        rendered.add(name)
            built.append(
                FormSectionSpec(
                    label=label or "",
                    description=str(opts.get("description") or ""),
                    fields=tuple(specs),
                )
            )
        flat = _flatten_fieldset_names(fieldsets)
        leftover = tuple(
            field_spec(form[name])
            for name in form.fields
            if name not in rendered and (not flat or name in flat)
        )
        if leftover:
            built.append(FormSectionSpec(label="", description="", fields=leftover))
        sections = tuple(built)

    non_field_errors = (
        tuple(str(e) for e in form.non_field_errors())
        if hasattr(form, "non_field_errors")
        else ()
    )
    return FormSpec(sections=sections, non_field_errors=non_field_errors)


__all__ = [
    "FieldKind",
    "FieldSpec",
    "FormSectionSpec",
    "FormSpec",
    "FormsetRowSpec",
    "FormsetSpec",
    "field_spec",
    "form_spec",
    "formset_spec",
]
