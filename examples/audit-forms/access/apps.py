from django.apps import AppConfig


class AccessConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "access"

    def ready(self) -> None:
        """Import receiver and backend modules so signals wire up at startup."""
        from access import backends, receivers  # noqa: F401, PLC0415
