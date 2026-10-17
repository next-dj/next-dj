from typing import ClassVar

from django.apps import AppConfig


class ShadcnAdminConfig(AppConfig):
    name = "shadcn_admin"
    label = "shadcn_admin"
    default_auto_field = "django.db.models.BigAutoField"
    mounts: ClassVar[dict[str, str]] = {"pages": "", "auth_pages": ""}
