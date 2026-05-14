from django.apps import AppConfig


class AdminAuditConfig(AppConfig):
    name = "admin_audit"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Import the receivers module so signal wiring fires at startup."""
        from admin_audit import signals  # noqa: F401, PLC0415
