from django.apps import apps
from django.contrib import admin
from django.contrib.admin.options import ModelAdmin
from django.db.models import Model
from django.http import Http404, HttpRequest
from django.urls import reverse


_NS = "next"
# Route names are auto-derived by the file router from the page tree:
# `prepare_url_name` snake-cases the URL path (parameters included), and
# `URL_NAME_TEMPLATE` prefixes with `page_`. Centralising the names here
# keeps the ugly-but-stable identifiers in one place.
_CHANGELIST_NAME = f"{_NS}:page_str_app_label_str_model_name"
_ADD_NAME = f"{_NS}:page_str_app_label_str_model_name_add"
_CHANGE_NAME = f"{_NS}:page_str_app_label_str_model_name_int_pk_change"
_DELETE_NAME = f"{_NS}:page_str_app_label_str_model_name_int_pk_delete"
_HISTORY_NAME = f"{_NS}:page_str_app_label_str_model_name_int_pk_history"
_DASHBOARD_NAME = f"{_NS}:page_"
_LOGIN_NAME = f"{_NS}:page_login"


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
        raise Http404
    return model, model_admin, obj


def dashboard_url() -> str:
    """URL of the admin dashboard (`/admin/`)."""
    return reverse(_DASHBOARD_NAME)


def login_url() -> str:
    """URL of the login page."""
    return reverse(_LOGIN_NAME)


def changelist_url(app_label: str, model_name: str) -> str:
    """URL of the changelist page for one model."""
    return reverse(
        _CHANGELIST_NAME,
        kwargs={"app_label": app_label, "model_name": model_name},
    )


def add_url(app_label: str, model_name: str) -> str:
    """URL of the add view for one model."""
    return reverse(
        _ADD_NAME,
        kwargs={"app_label": app_label, "model_name": model_name},
    )


def change_url(app_label: str, model_name: str, pk: int | str) -> str:
    """URL of the change view for one model instance."""
    return reverse(
        _CHANGE_NAME,
        kwargs={"app_label": app_label, "model_name": model_name, "pk": pk},
    )


def delete_url(app_label: str, model_name: str, pk: int | str) -> str:
    """URL of the delete confirmation view for one model instance."""
    return reverse(
        _DELETE_NAME,
        kwargs={"app_label": app_label, "model_name": model_name, "pk": pk},
    )
