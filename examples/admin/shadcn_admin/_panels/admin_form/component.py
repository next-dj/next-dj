from dataclasses import dataclass
from typing import Any, ClassVar

from django import forms as django_forms
from django.contrib import messages
from django.contrib.admin.options import ModelAdmin
from django.contrib.admin.utils import flatten_fieldsets
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.core.exceptions import ValidationError
from django.db.models import Model
from django.forms.models import BaseInlineFormSet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from shadcn_admin import utils

from next.components import component
from next.forms import BaseModelForm, action


_LOG_CHANGE_MESSAGE = "Changed via shadcn-admin form"


@dataclass(frozen=True, slots=True)
class AdminFormSpec:
    """Frozen bundle of everything an admin form view needs per request.

    Resolved once in factory / context / handler and passed around as a
    single value so `get_object` and `get_form` are not called twice.
    """

    request: HttpRequest
    app_label: str
    model_name: str
    model: type[Model]
    model_admin: ModelAdmin
    instance: Model | None

    @classmethod
    def resolve(
        cls,
        request: HttpRequest,
        app_label: str,
        model_name: str,
        pk: int | None = None,
    ) -> "AdminFormSpec":
        """Look up `(model, ModelAdmin, instance)` once and freeze them into a spec."""
        if pk is None:
            model, model_admin = utils.resolve_model_admin(app_label, model_name)
            instance: Model | None = None
        else:
            model, model_admin, instance = utils.resolve_object_or_404(
                request, app_label, model_name, pk
            )
        return cls(
            request=request,
            app_label=app_label,
            model_name=model_name,
            model=model,
            model_admin=model_admin,
            instance=instance,
        )

    @property
    def is_change(self) -> bool:
        """`True` for the change view (`instance` is set), `False` for add."""
        return self.instance is not None

    @property
    def changelist_url(self) -> str:
        """URL of the changelist this form returns to on plain Save."""
        return utils.changelist_url(self.app_label, self.model_name)

    @property
    def add_url(self) -> str:
        """URL of the add view (used by Save-and-add-another)."""
        return utils.add_url(self.app_label, self.model_name)

    def change_url(self, obj: Model) -> str:
        """URL of the change view for the just-saved object."""
        return utils.change_url(self.app_label, self.model_name, obj.pk)

    @property
    def delete_url(self) -> str | None:
        """URL of the delete view when this spec carries an instance."""
        if self.instance is None:
            return None
        return utils.delete_url(self.app_label, self.model_name, self.instance.pk)


@dataclass(frozen=True, slots=True)
class WidgetInfo:
    """Normalized widget descriptor passed to `form_field/component.djx`.

    `kind` is the only branch the template inspects (textarea / checkbox /
    select / select_multi / input). `input_type` is set when kind=input
    so the template emits the right HTML5 type without re-scanning widget
    class names.
    """

    field: django_forms.BoundField
    kind: str
    input_type: str
    selected_strs: tuple[str, ...]
    is_extra: bool

    @classmethod
    def from_bound(
        cls,
        bound_field: django_forms.BoundField,
        *,
        is_extra: bool = False,
    ) -> "WidgetInfo":
        """Inspect the bound field's widget once and produce a normalized descriptor.

        `isinstance` checks cover both stock Django widgets and the
        `Admin*` subclasses Django swaps in for `ModelAdmin.get_form`
        (`AdminTextareaWidget`, `AdminDateWidget`, …). `input_type`
        comes straight from the widget so HTML5 types stay in sync.
        """
        widget = bound_field.field.widget
        if isinstance(widget, RelatedFieldWidgetWrapper):
            widget = widget.widget
        if isinstance(widget, django_forms.Textarea):
            kind, input_type = "textarea", ""
        elif isinstance(widget, django_forms.CheckboxInput):
            kind, input_type = "checkbox", ""
        elif isinstance(widget, django_forms.SelectMultiple):
            kind, input_type = "select_multi", ""
        elif isinstance(widget, django_forms.Select):
            kind, input_type = "select", ""
        else:
            kind = "input"
            input_type = getattr(widget, "input_type", "text")
        selected: tuple[str, ...] = ()
        if kind == "select_multi":
            raw = bound_field.value() or []
            if not isinstance(raw, (list, tuple)):
                raw = [raw]  # pragma: no cover
            selected = tuple(str(v) for v in raw)
        return cls(
            field=bound_field,
            kind=kind,
            input_type=input_type,
            selected_strs=selected,
            is_extra=is_extra,
        )


def _build_inline_formsets(spec: AdminFormSpec) -> list[BaseInlineFormSet]:
    formsets: list[BaseInlineFormSet] = []
    parent = spec.instance
    for inline in spec.model_admin.get_inline_instances(spec.request, parent):
        formset_cls = inline.get_formset(spec.request, parent)
        prefix = formset_cls.get_default_prefix()
        if spec.request.method == "POST":
            fs = formset_cls(
                spec.request.POST,
                spec.request.FILES,
                instance=parent,
                prefix=prefix,
            )
        else:
            fs = formset_cls(instance=parent, prefix=prefix)
        # Extra rows ship blank: drop model-default initial so the user sees
        # empty inputs and submitting unfilled rows does not flip
        # `has_changed()` to True (which would otherwise trigger required-
        # field validation on rows the user intended to skip).
        for inline_form in fs.forms:
            if inline_form.empty_permitted and not inline_form.instance.pk:
                inline_form.initial = {}
                for fld in inline_form.fields.values():
                    fld.initial = None
        formsets.append(fs)
    return formsets


def _build_form_class(spec: AdminFormSpec) -> type[django_forms.Form]:
    """Wrap `ModelAdmin.get_form` so inline-formset errors surface on the main form.

    `AdminForm.clean()` builds the inline formsets a second time bound to
    the same POST data, calls `is_valid()` on each, and raises
    `ValidationError` if any inline row is broken. The dispatcher catches
    that through `form.is_valid() == False` and re-renders the origin page
    — `form_state` then rebuilds the formsets and shows the errors next to
    the bad rows.
    """
    base = spec.model_admin.get_form(spec.request, spec.instance, change=spec.is_change)

    class AdminForm(base, BaseModelForm):  # type: ignore[misc, valid-type]
        _admin_spec: ClassVar[AdminFormSpec] = spec

        @classmethod
        def get_initial(cls) -> Model | None:
            return spec.instance

        def clean(self) -> dict[str, Any] | None:
            cleaned = super().clean()
            errors: list[ValidationError] = []
            for fs in _build_inline_formsets(spec):
                if not fs.is_valid():
                    for inline_form in fs.forms:
                        for field_errors in inline_form.errors.values():
                            errors.extend(field_errors)
                    errors.extend(fs.non_form_errors())
            if errors:
                raise ValidationError(errors)
            return cleaned

    AdminForm.__name__ = f"Admin{base.__name__}"
    return AdminForm


def admin_add_form_factory(
    request: HttpRequest,
    app_label: str,
    model_name: str,
) -> type[django_forms.Form]:
    """Per-request form class for the add view."""
    return _build_form_class(AdminFormSpec.resolve(request, app_label, model_name))


def admin_change_form_factory(
    request: HttpRequest,
    app_label: str,
    model_name: str,
    pk: int,
) -> type[django_forms.Form]:
    """Per-request form class for the change view."""
    return _build_form_class(
        AdminFormSpec.resolve(request, app_label, model_name, pk=pk)
    )


def _build_sections(
    spec: AdminFormSpec,
    bound: django_forms.Form,
) -> list[dict[str, Any]]:
    fieldsets = spec.model_admin.get_fieldsets(spec.request, spec.instance)
    rendered: set[str] = set()
    sections: list[dict[str, Any]] = []
    for label, opts in fieldsets:
        infos = [
            WidgetInfo.from_bound(bound[name])
            for name in opts.get("fields", [])
            if name in bound.fields
        ]
        for info in infos:
            rendered.add(info.field.name)
        sections.append(
            {
                "label": label or "",
                "description": opts.get("description") or "",
                "fields": infos,
            }
        )
    flat = flatten_fieldsets(fieldsets) if fieldsets else []
    leftover = [
        WidgetInfo.from_bound(bound[name])
        for name in bound.fields
        if name not in rendered and (not flat or name in flat)
    ]
    if leftover:  # pragma: no cover
        sections.append({"label": "", "description": "", "fields": leftover})
    return sections


def _serialize_formset(formset: BaseInlineFormSet) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for inline_form in formset.forms:
        is_extra = inline_form.empty_permitted and not inline_form.instance.pk
        visible_names = [
            name
            for name in inline_form.fields
            if name != "DELETE" and not inline_form[name].is_hidden
        ]
        hidden_html = "".join(
            str(inline_form[name])
            for name in inline_form.fields
            if inline_form[name].is_hidden
        )
        rows.append(
            {
                "fields": [
                    WidgetInfo.from_bound(inline_form[name], is_extra=is_extra)
                    for name in visible_names
                ],
                "hidden_html": hidden_html,
                "delete_field": (
                    inline_form["DELETE"] if "DELETE" in inline_form.fields else None
                ),
                "errors": inline_form.errors,
            }
        )
    return {
        "prefix": formset.prefix,
        "verbose_name_plural": str(formset.model._meta.verbose_name_plural),
        "management_form": formset.management_form,
        "rows": rows,
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
    spec = AdminFormSpec.resolve(request, app_label, model_name, pk=pk)
    form_cls = spec.model_admin.get_form(
        spec.request, spec.instance, change=spec.is_change
    )
    if request.method == "POST":
        bound = form_cls(request.POST, request.FILES, instance=spec.instance)
        bound.is_valid()
    else:
        bound = form_cls(instance=spec.instance)
    sections = _build_sections(spec, bound)
    formsets = _build_inline_formsets(spec)
    if request.method == "POST":  # pragma: no cover
        for fs in formsets:
            fs.is_valid()
    return {
        "app_label": spec.app_label,
        "model_name": spec.model_name,
        "pk": pk,
        "is_change": spec.is_change,
        "verbose_name": str(spec.model._meta.verbose_name),
        "form": bound,
        "sections": sections,
        "inlines": [_serialize_formset(fs) for fs in formsets],
        "non_field_errors": (
            bound.non_field_errors() if hasattr(bound, "non_field_errors") else None
        ),
        "action_name": "admin:change" if spec.is_change else "admin:add",
        "title": "Edit" if spec.is_change else "Add",
        "changelist_url": spec.changelist_url,
        "delete_url": spec.delete_url,
    }


def _persist(
    form: django_forms.ModelForm,
    spec: AdminFormSpec,
    *,
    change: bool,
) -> HttpResponse:
    obj = form.save(commit=False)
    formsets = _build_inline_formsets(spec)
    for fs in formsets:
        fs.is_valid()
    spec.model_admin.save_model(spec.request, obj, form, change=change)
    form.save_m2m()
    for fs in formsets:
        fs.instance = obj
        fs.save()
    if change:
        spec.model_admin.log_change(spec.request, obj, _LOG_CHANGE_MESSAGE)
    else:
        spec.model_admin.log_addition(spec.request, obj, [{"added": {}}])
    verb = "updated" if change else "added"
    messages.success(
        spec.request,
        f"The {spec.model._meta.verbose_name} {obj} was {verb} successfully.",
    )
    return _redirect_after_save(spec, obj)


def _redirect_after_save(spec: AdminFormSpec, obj: Model) -> HttpResponseRedirect:
    post = spec.request.POST
    if "_save_continue" in post:
        return HttpResponseRedirect(spec.change_url(obj))
    if "_save_addanother" in post:
        return HttpResponseRedirect(spec.add_url)
    return HttpResponseRedirect(spec.changelist_url)


@action("admin:add", form_class=admin_add_form_factory)
def handle_add(
    request: HttpRequest,
    form: django_forms.ModelForm,
    app_label: str,
    model_name: str,
) -> HttpResponse:
    """Save a new object and its inline formsets, then redirect."""
    spec = AdminFormSpec.resolve(request, app_label, model_name)
    return _persist(form, spec, change=False)


@action("admin:change", form_class=admin_change_form_factory)
def handle_change(
    request: HttpRequest,
    form: django_forms.ModelForm,
    app_label: str,
    model_name: str,
    pk: int,
) -> HttpResponse:
    """Persist main form and inline formsets via `ModelAdmin.save_model`.

    `pk` is part of the action signature so the URL kwarg is echoed back
    as a `_url_param_pk` hidden field on the rendered form. The instance
    comes from `admin_change_form_factory` through `get_initial`.
    """
    spec = AdminFormSpec.resolve(request, app_label, model_name, pk=pk)
    return _persist(form, spec, change=True)
