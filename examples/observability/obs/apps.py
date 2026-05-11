from importlib import import_module

from django.apps import AppConfig

from next.static import default_kinds


class ObsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "obs"

    def ready(self) -> None:
        """Connect signal receivers and register the JSX asset kind."""
        default_kinds.register(
            "jsx",
            extension=".jsx",
            slot="scripts",
            renderer="render_babel_script_tag",
        )
        import_module(f"{self.name}.receivers")
