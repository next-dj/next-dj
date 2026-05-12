from typing import Any

from django.contrib.admin.utils import get_deleted_objects
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from shadcn_admin.utils import resolve_model_admin

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
    model, model_admin = resolve_model_admin(app_label, model_name)
    obj = model_admin.get_object(request, pk)
    if obj is None:
        raise Http404
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
        "changelist_url": f"/admin/{app_label}/{model_name}/",
        "change_url": f"/admin/{app_label}/{model_name}/{pk}/change/",
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
    _, model_admin = resolve_model_admin(app_label, model_name)
    obj = model_admin.get_object(request, pk)
    if obj is None:
        raise Http404
    model_admin.log_deletions(request, [obj])
    model_admin.delete_model(request, obj)
    return HttpResponseRedirect(f"/admin/{app_label}/{model_name}/")
