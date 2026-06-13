from typing import Any

from django.http import HttpRequest
from shadcn_admin.forms import AdminFormSpec, build_inline_formsets

from next.components import component
from next.deps import Depends
from next.forms import form_spec, formset_spec


@component.context("form_state")
def form_state(
    request: HttpRequest,
    spec: AdminFormSpec = Depends("admin_spec"),
    pk: int | None = None,
) -> dict[str, Any]:
    """Build add/change context — binds the form to POST so re-render shows errors."""
    form_cls = spec.model_admin.get_form(
        spec.request, spec.instance, change=spec.is_change
    )
    if request.method == "POST":
        bound = form_cls(request.POST, request.FILES, instance=spec.instance)
        bound.is_valid()
    else:
        bound = form_cls(instance=spec.instance)
    fieldsets = spec.model_admin.get_fieldsets(spec.request, spec.instance)
    formsets = build_inline_formsets(spec)
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
        "form_spec": form_spec(bound, fieldsets=fieldsets),
        "inlines": [formset_spec(fs) for fs in formsets],
        "action_name": "admin:change" if spec.is_change else "admin:add",
        "title": "Edit" if spec.is_change else "Add",
        "changelist_url": spec.changelist_url,
        "delete_url": spec.delete_url,
    }
