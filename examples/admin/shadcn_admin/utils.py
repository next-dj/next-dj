from django.apps import apps
from django.contrib import admin
from django.contrib.admin.options import ModelAdmin
from django.db.models import Model
from django.http import Http404, HttpRequest

from next.urls import page_reverse


# Path prefixes the auth middleware compares against. Kept as literals
# because `path.startswith(...)` runs on every request and can't accept
# a lazy reverse result.
ADMIN_PREFIX = "/admin/"
LOGIN_URL = "/admin/login/"


def resolve_model_admin(
    app_label: str,
    model_name: str,
) -> tuple[type[Model], ModelAdmin]:
    """Return `(model, ModelAdmin)` from `admin.site._registry`, or 404."""
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError as exc:
        msg = str(exc)
        raise Http404(msg) from exc
    # admin.site._registry is Django-internal but unavoidable for an
    # admin-style enumeration. No public API exposes the registered map.
    model_admin = admin.site._registry.get(model)
    if model_admin is None:
        msg = f"Model {app_label}.{model_name} is not registered with admin."
        raise Http404(msg)
    return model, model_admin


def resolve_object_or_404(
    request: HttpRequest,
    app_label: str,
    model_name: str,
    pk: int,
) -> tuple[type[Model], ModelAdmin, Model]:
    """Like `resolve_model_admin` but also fetches the object — 404 if missing."""
    model, model_admin = resolve_model_admin(app_label, model_name)
    obj = model_admin.get_object(request, pk)
    if obj is None:
        msg = f"Object {app_label}.{model_name} with pk={pk} not found."
        raise Http404(msg)
    return model, model_admin, obj


def dashboard_url() -> str:
    """URL of the admin dashboard (`/admin/`)."""
    return page_reverse()


def login_url() -> str:
    """URL of the login page."""
    return page_reverse("login")


def changelist_url(app_label: str, model_name: str) -> str:
    """URL of the changelist page for one model."""
    return page_reverse(
        "[str:app_label]/[str:model_name]",
        app_label=app_label,
        model_name=model_name,
    )


def add_url(app_label: str, model_name: str) -> str:
    """URL of the add view for one model."""
    return page_reverse(
        "[str:app_label]/[str:model_name]/add",
        app_label=app_label,
        model_name=model_name,
    )


def change_url(app_label: str, model_name: str, pk: int | str) -> str:
    """URL of the change view for one model instance."""
    return page_reverse(
        "[str:app_label]/[str:model_name]/[int:pk]/change",
        app_label=app_label,
        model_name=model_name,
        pk=pk,
    )


def delete_url(app_label: str, model_name: str, pk: int | str) -> str:
    """URL of the delete confirmation view for one model instance."""
    return page_reverse(
        "[str:app_label]/[str:model_name]/[int:pk]/delete",
        app_label=app_label,
        model_name=model_name,
        pk=pk,
    )
