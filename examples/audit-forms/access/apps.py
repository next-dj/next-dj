from django.apps import AppConfig


class AccessConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "access"
    label = "access"

    def ready(self) -> None:
        """Import signal and backend modules so signals wire up at startup."""
        from access import backends, signals
