from django.apps import AppConfig


class MyappConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "myapp"
    label = "myapp"

    def ready(self) -> None:
        """Import the app's `deps` and `signals` modules here once it grows them."""
