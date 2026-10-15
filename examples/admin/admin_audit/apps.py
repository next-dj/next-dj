from django.apps import AppConfig


class AdminAuditConfig(AppConfig):
    name = "admin_audit"
    label = "admin_audit"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Import the signals module so its receivers connect at startup."""
        from admin_audit import signals
