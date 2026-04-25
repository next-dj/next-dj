from django.apps import AppConfig


class ShortenerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shortener"

    def ready(self) -> None:
        """Register DI providers and connect the action-dispatch receiver."""
        from shortener import providers, receivers  # noqa: F401, PLC0415
