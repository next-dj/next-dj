from django.apps import AppConfig


class NotesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notes"

    def ready(self) -> None:
        """Import provider modules so DI auto-registry wires up at startup."""
        from notes import providers  # noqa: F401, PLC0415
