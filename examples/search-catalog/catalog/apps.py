from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"

    def ready(self) -> None:
        """Import providers so they auto-register on startup."""
        from catalog import providers  # noqa: F401, PLC0415
