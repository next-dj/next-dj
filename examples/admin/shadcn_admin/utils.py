"""Small shared helper used by every admin page."""

from django.apps import apps
from django.contrib import admin
from django.contrib.admin.options import ModelAdmin
from django.db.models import Model
from django.http import Http404


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
