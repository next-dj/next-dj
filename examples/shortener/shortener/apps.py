from django.apps import AppConfig


class ShortenerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shortener"
    label = "shortener"

    def ready(self) -> None:
        """Register DI providers and connect the action-dispatch receiver."""
        from shortener import deps, signals
