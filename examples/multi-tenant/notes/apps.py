from django.apps import AppConfig


class NotesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notes"
    label = "notes"

    def ready(self) -> None:
        """Import the DI and signal modules so they register at startup."""
        from notes import deps, signals
