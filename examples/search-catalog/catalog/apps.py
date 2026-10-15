from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"
    label = "catalog"

    def ready(self) -> None:
        """Import the DI module so its providers auto-register on startup."""
        from catalog import deps
