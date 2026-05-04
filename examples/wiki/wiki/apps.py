from django.apps import AppConfig


class WikiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "wiki"

    def ready(self) -> None:
        """Wire DI providers and signal receivers when the app loads."""
        from wiki import providers, receivers  # noqa: F401, PLC0415
