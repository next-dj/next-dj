"""Add/change form composite for `/admin/<app>/<model>/{add,pk/change}/`.

`@component.context("form_state")` builds the per-request dict the template
branches on. The same callable runs on GET (unbound form, unbound formsets)
and on POST (bound, so a re-render after an error keeps the user's input).

`admin_add_form_factory` / `admin_change_form_factory` are passed to
`@forms.action(..., form_class=...)` and resolved per request by the
dispatcher; they call `ModelAdmin.get_form(request, obj, change=...)`.

The `admin:add` and `admin:change` action handlers persist the main form
and inline formsets through `ModelAdmin.save_model` / `save_m2m` / `save`.
"""

from typing import Any

from django import forms as django_forms
from django.contrib.admin.options import ModelAdmin
from django.contrib.admin.utils import flatten_fieldsets
from django.db.models import Model
from django.forms.models import BaseInlineFormSet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from shadcn_admin.utils import resolve_model_admin

from next.components import component
from next.forms import BaseModelForm, action


def _wrap_with_get_initial(
    base: type[django_forms.Form],
    instance: Model | None,
) -> type[django_forms.Form]:
    """Subclass so dispatch picks up the model instance through `get_initial`."""

    class AdminForm(base, BaseModelForm):  # type: ignore[misc, valid-type]
        @classmethod
        def get_initial(cls) -> Model | None:
            return instance

    AdminForm.__name__ = f"Admin{base.__name__}"
    return AdminForm


def admin_add_form_factory(
    request: HttpRequest,
    app_label: str,
    model_name: str,
) -> type[django_forms.Form]:
    """Per-request form class for the add view."""
    _, model_admin = resolve_model_admin(app_label, model_name)
    return _wrap_with_get_initial(
        model_admin.get_form(request, None, change=False),
        instance=None,
    )


def admin_change_form_factory(
    request: HttpRequest,
    app_label: str,
    model_name: str,
    pk: int,
) -> type[django_forms.Form]:
    """Per-request form class for the change view."""
    _, model_admin = resolve_model_admin(app_label, model_name)
    obj = model_admin.get_object(request, pk)
    return _wrap_with_get_initial(
        model_admin.get_form(request, obj, change=True),
        instance=obj,
    )


def _build_inline_formsets(
    request: HttpRequest,
    model_admin: ModelAdmin,
    instance: Model | None,
    *,
    change: bool,
) -> list[BaseInlineFormSet]:
    formsets: list[BaseInlineFormSet] = []
    parent_for_inlines = instance if change else None
    for inline in model_admin.get_inline_instances(request, parent_for_inlines):
        FormSetClass = inline.get_formset(  # noqa: N806
            request,
            parent_for_inlines,
        )
        prefix = FormSetClass.get_default_prefix()
        if request.method == "POST":
            fs = FormSetClass(
                request.POST,
                request.FILES,
                instance=instance,
                prefix=prefix,
            )
        else:
            fs = FormSetClass(instance=instance, prefix=prefix)
        # Extra rows ship blank: drop model-default initial so the user sees
        # empty inputs and submitting unfilled rows does not flip
        # `has_changed()` to True (which would otherwise trigger required-
        # field validation on rows the user intended to skip).
        for form in fs.forms:
            if form.empty_permitted and not form.instance.pk:
                form.initial = {}
                for fld in form.fields.values():
                    fld.initial = None
        formsets.append(fs)
    return formsets


def _field_info(bound_field: django_forms.BoundField) -> dict[str, Any]:
    widget = bound_field.field.widget
    widget_name = widget.__class__.__name__
    if widget_name == "RelatedFieldWidgetWrapper":
        widget_name = widget.widget.__class__.__name__
    multi = {
        "SelectMultiple",
        "FilteredSelectMultiple",
        "CheckboxSelectMultiple",
        "AutocompleteSelectMultiple",
    }
    selected_strs: list[str] = []
    if widget_name in multi:
        raw = bound_field.value() or []
        if not isinstance(raw, (list, tuple)):  # pragma: no cover
            raw = [raw]
        selected_strs = [str(v) for v in raw]
    return {
        "field": bound_field,
        "widget_name": widget_name,
        "selected_strs": selected_strs,
    }


def _serialize_formset(formset: BaseInlineFormSet) -> dict[str, Any]:
    def _info(
        form: django_forms.BaseForm,
        name: str,
        *,
        is_extra: bool,
    ) -> dict[str, Any]:
        bf = form[name]
        widget_name = bf.field.widget.__class__.__name__
        if widget_name == "RelatedFieldWidgetWrapper":  # pragma: no cover
            widget_name = bf.field.widget.widget.__class__.__name__
        return {
            "field": bf,
            "widget_name": widget_name,
            "selected_strs": [],
            "is_extra": is_extra,
        }

    def _row(form: django_forms.BaseForm) -> dict[str, Any]:
        is_extra = form.empty_permitted and not form.instance.pk
        visible = [
            name
            for name in form.fields
            if name != "DELETE" and not form[name].is_hidden
        ]
        hidden_html = "".join(
            str(form[name]) for name in form.fields if form[name].is_hidden
        )
        return {
            "fields": [_info(form, name, is_extra=is_extra) for name in visible],
            "hidden_html": hidden_html,
            "delete_field": form["DELETE"] if "DELETE" in form.fields else None,
            "errors": form.errors,
        }

    return {
        "prefix": formset.prefix,
        "verbose_name_plural": str(formset.model._meta.verbose_name_plural),
        "management_form": formset.management_form,
        "rows": [_row(f) for f in formset.forms],
        "non_form_errors": list(formset.non_form_errors()),
        "can_delete": formset.can_delete,
    }


@component.context("form_state")
def form_state(
    request: HttpRequest,
    app_label: str,
    model_name: str,
    pk: int | None = None,
) -> dict[str, Any]:
    """Build add/change context — binds the form to POST so re-render shows errors."""
    model, model_admin = resolve_model_admin(app_label, model_name)
    obj = model_admin.get_object(request, pk) if pk is not None else None
    form_cls = model_admin.get_form(request, obj, change=pk is not None)
    if request.method == "POST":
        bound = form_cls(request.POST, request.FILES, instance=obj)
        bound.is_valid()
    else:
        bound = form_cls(instance=obj)

    fieldsets = model_admin.get_fieldsets(request, obj)
    rendered: set[str] = set()
    sections: list[dict[str, Any]] = []
    for label, opts in fieldsets:
        fields = [
            _field_info(bound[name])
            for name in opts.get("fields", [])
            if name in bound.fields
        ]
        for info in fields:
            rendered.add(info["field"].name)
        sections.append(
            {
                "label": label or "",
                "description": opts.get("description") or "",
                "fields": fields,
            }
        )
    flat = flatten_fieldsets(fieldsets) if fieldsets else []
    leftover = [
        _field_info(bound[name])
        for name in bound.fields
        if name not in rendered and (not flat or name in flat)
    ]
    if leftover:  # pragma: no cover
        sections.append({"label": "", "description": "", "fields": leftover})

    formsets = _build_inline_formsets(request, model_admin, obj, change=pk is not None)
    if request.method == "POST":  # pragma: no cover
        for fs in formsets:
            fs.is_valid()
    inlines = [_serialize_formset(fs) for fs in formsets]

    is_change = pk is not None
    return {
        "app_label": app_label,
        "model_name": model_name,
        "pk": pk,
        "is_change": is_change,
        "verbose_name": str(model._meta.verbose_name),
        "form": bound,
        "sections": sections,
        "inlines": inlines,
        "non_field_errors": (
            bound.non_field_errors() if hasattr(bound, "non_field_errors") else None
        ),
        "action_name": "admin:change" if is_change else "admin:add",
        "title": "Edit" if is_change else "Add",
        "changelist_url": f"/admin/{app_label}/{model_name}/",
        "delete_url": (
            f"/admin/{app_label}/{model_name}/{pk}/delete/" if is_change else None
        ),
    }


@action("admin:add", form_class=admin_add_form_factory)
def handle_add(
    request: HttpRequest,
    form: django_forms.ModelForm,
    app_label: str,
    model_name: str,
) -> HttpResponse:
    """Save a new object and its inline formsets, then redirect to changelist."""
    _, model_admin = resolve_model_admin(app_label, model_name)
    new_obj = form.save(commit=False)
    formsets = _build_inline_formsets(request, model_admin, new_obj, change=False)
    if not all(fs.is_valid() for fs in formsets):
        return HttpResponse(status=400, content="Inline formset validation failed")
    model_admin.save_model(request, new_obj, form, change=False)
    form.save_m2m()
    for fs in formsets:
        fs.instance = new_obj
        fs.save()
    model_admin.log_addition(request, new_obj, [{"added": {}}])
    return HttpResponseRedirect(f"/admin/{app_label}/{model_name}/")


@action("admin:change", form_class=admin_change_form_factory)
def handle_change(
    request: HttpRequest,
    form: django_forms.ModelForm,
    app_label: str,
    model_name: str,
    pk: int,
) -> HttpResponse:
    """Persist main form and inline formsets via `ModelAdmin.save_model`.

    `pk` is part of the action signature so the URL kwarg is echoed back as
    a `_url_param_pk` hidden field on the rendered form; the instance comes
    from the `admin_change_form_factory` via `get_initial`, so we don't read
    `pk` again here.
    """
    del pk
    _, model_admin = resolve_model_admin(app_label, model_name)
    obj = form.save(commit=False)
    formsets = _build_inline_formsets(request, model_admin, obj, change=True)
    if not all(fs.is_valid() for fs in formsets):
        return HttpResponse(status=400, content="Inline formset validation failed")
    model_admin.save_model(request, obj, form, change=True)
    form.save_m2m()
    for fs in formsets:
        fs.instance = obj
        fs.save()
    model_admin.log_change(request, obj, "Changed via shadcn-admin form")
    return HttpResponseRedirect(f"/admin/{app_label}/{model_name}/")
