from typing import Any

from django.apps import apps
from django.contrib import admin
from django.http import HttpRequest

from next.pages import context


_AUTH_PREFIXES = ("/admin/login/", "/admin/logout/")


@context("app_list", inherit_context=True)
def app_list(request: HttpRequest) -> list[dict[str, Any]]:
    """Return the app/model tree visible to `request` for sidebar and dashboard."""
    apps_index: dict[str, dict[str, Any]] = {}
    for model, model_admin in admin.site._registry.items():
        if not model_admin.has_module_permission(request):
            continue
        perms = model_admin.get_model_perms(request)
        if True not in perms.values():  # pragma: no cover
            continue
        app_label = model._meta.app_label
        app = apps_index.setdefault(
            app_label,
            {
                "name": apps.get_app_config(app_label).verbose_name,
                "app_label": app_label,
                "models": [],
            },
        )
        model_name = model._meta.model_name
        app["models"].append(
            {
                "name": model._meta.verbose_name_plural,
                "object_name": model._meta.object_name,
                "model_name": model_name,
                "admin_url": f"/admin/{app_label}/{model_name}/",
                "add_url": (
                    f"/admin/{app_label}/{model_name}/add/"
                    if perms.get("add")
                    else None
                ),
            }
        )
    for app in apps_index.values():
        app["models"].sort(key=lambda m: m["name"])
    return sorted(apps_index.values(), key=lambda a: a["name"].lower())


@context("is_auth_page", inherit_context=True)
def is_auth_page(request: HttpRequest) -> bool:
    """Flag pages that render with the centered auth chrome instead of admin shell."""
    return any(request.path.startswith(p) for p in _AUTH_PREFIXES)
