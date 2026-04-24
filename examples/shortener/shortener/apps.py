from django.apps import AppConfig


class ShortenerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shortener"

    def ready(self) -> None:
        """Import the provider module so the DI resolver registers it."""
        from shortener import providers  # noqa: F401, PLC0415
