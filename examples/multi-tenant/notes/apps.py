from django.apps import AppConfig


class NotesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notes"

    def ready(self) -> None:
        """Import provider and receiver modules so DI and signals wire up at startup."""
        from notes import providers, receivers  # noqa: F401, PLC0415
