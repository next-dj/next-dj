from django.apps import AppConfig


class FlagsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flags"
    label = "flags"

    def ready(self) -> None:
        """Import the `deps` and `signals` modules so DI and receivers wire up."""
        from flags import deps, signals
