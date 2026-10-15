from django.apps import AppConfig


class ShadcnAdminConfig(AppConfig):
    name = "shadcn_admin"
    label = "shadcn_admin"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Register the named `admin_spec` dependency."""
        from shadcn_admin import deps
