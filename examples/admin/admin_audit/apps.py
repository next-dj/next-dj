from typing import ClassVar

from django.apps import AppConfig


class AdminAuditConfig(AppConfig):
    name = "admin_audit"
    label = "admin_audit"
    default_auto_field = "django.db.models.BigAutoField"
    depends_on = ("shadcn_admin",)
    isolated = True
    mounts: ClassVar[dict[str, str]] = {
        "pages": "activity/",
        "object_pages": "<str:app_label>/<str:model_name>/<int:pk>/",
    }
