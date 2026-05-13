from typing import Any

from django.contrib import messages
from django.contrib.admin.utils import get_deleted_objects
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from shadcn_admin import utils

from next.forms import action
from next.pages import context


@context("delete_state")
def delete_state(
    request: HttpRequest,
    app_label: str,
    model_name: str,
    pk: int,
) -> dict[str, Any]:
    """Build confirmation context: object, deps, protected refs, permissions."""
    model, model_admin, obj = utils.resolve_object_or_404(
        request, app_label, model_name, pk
    )
    _to_delete, model_count, perms_needed, protected = get_deleted_objects(
        [obj],
        request,
        model_admin.admin_site,
    )
    return {
        "app_label": app_label,
        "model_name": model_name,
        "pk": pk,
        "obj": obj,
        "obj_repr": str(obj),
        "verbose_name": str(model._meta.verbose_name),
        "model_count": list(model_count.items()),
        "perms_needed": sorted(perms_needed),
        "protected": [str(p) for p in protected],
        "changelist_url": utils.changelist_url(app_label, model_name),
        "change_url": utils.change_url(app_label, model_name, pk),
        "can_delete": (
            model_admin.has_delete_permission(request, obj)
            and not perms_needed
            and not protected
        ),
    }


@action("admin:delete")
def delete(
    request: HttpRequest,
    app_label: str,
    model_name: str,
    pk: int,
) -> HttpResponse:
    """Delete the object via `ModelAdmin.delete_model` and redirect."""
    model, model_admin, obj = utils.resolve_object_or_404(
        request, app_label, model_name, pk
    )
    obj_repr = str(obj)
    model_admin.log_deletions(request, [obj])
    model_admin.delete_model(request, obj)
    messages.success(
        request,
        f"The {model._meta.verbose_name} {obj_repr} was deleted successfully.",
    )
    return HttpResponseRedirect(utils.changelist_url(app_label, model_name))
