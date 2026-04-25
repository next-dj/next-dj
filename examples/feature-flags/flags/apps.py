from django.apps import AppConfig


class FlagsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flags"

    def ready(self) -> None:
        """Import provider and receiver modules so DI and signals wire up."""
        from flags import providers, receivers  # noqa: F401, PLC0415
