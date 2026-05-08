from importlib import import_module

from django.apps import AppConfig


class ObsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "obs"

    def ready(self) -> None:
        """Connect signal receivers once the app registry is populated."""
        import_module(f"{self.name}.receivers")
