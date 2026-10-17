import copy
from typing import Any

from django.contrib.auth import get_permission_codename
from django.http import HttpRequest
from shadcn_admin.admin import RowPermissionAdmin
from shadcn_admin.forms import AdminFormSpec, AdminInlineSpec, build_inline_formsets

from next import Depends, component
from next.forms import form_spec, formset_spec


@component.context("form_state")
def form_state(
    request: HttpRequest,
    spec: AdminFormSpec = Depends("admin_spec"),
    pk: int | None = None,
) -> dict[str, Any]:
    """Build add/change context, binding the form to POST so re-render shows errors."""
    form_cls = spec.model_admin.get_form(
        spec.request, spec.stored, change=spec.is_change
    )
    working = copy.copy(spec.stored)
    if request.method == "POST":
        bound = form_cls(
            request.POST, request.FILES, instance=working, auto_id=spec.auto_id
        )
        bound.is_valid()
    else:
        bound = form_cls(instance=working, auto_id=spec.auto_id)
    fieldsets = spec.model_admin.get_fieldsets(spec.request, spec.stored)
    opts = spec.model._meta
    row_rules = isinstance(spec.model_admin, RowPermissionAdmin)
    if spec.is_change and row_rules:
        object_links = spec.model_admin.get_object_links(spec.request, spec.stored)
    else:
        object_links = []
    if spec.is_change:
        inlines: list[Any] = []
        related_sections = AdminInlineSpec.build_sections(spec)
    else:
        formsets = build_inline_formsets(spec)
        if request.method == "POST":  # pragma: no cover
            for fs in formsets:
                fs.is_valid()
        inlines = [formset_spec(fs) for fs in formsets]
        related_sections = []
    return {
        "app_label": spec.app_label,
        "model_name": spec.model_name,
        "pk": pk,
        "is_change": spec.is_change,
        "instance": spec.stored,
        "row_rules": row_rules,
        "object_links": object_links,
        "change_perm": f"{opts.app_label}.{get_permission_codename('change', opts)}",
        "delete_perm": f"{opts.app_label}.{get_permission_codename('delete', opts)}",
        "verbose_name": str(spec.model._meta.verbose_name),
        "form": bound,
        "form_spec": form_spec(bound, fieldsets=fieldsets),
        "inlines": inlines,
        "related_sections": related_sections,
        "action_name": "admin:change" if spec.is_change else "admin:add",
        "title": "Edit" if spec.is_change else "Add",
        "changelist_url": spec.changelist_url,
        "delete_url": spec.delete_url,
    }
