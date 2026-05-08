from django.apps import AppConfig

from next.static import default_kinds
from next.static.discovery import default_stems


class PollsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "polls"

    def ready(self) -> None:
        """Register the Vue asset kind, the page stem, and signal wiring."""
        default_kinds.register(
            "vue",
            extension=".vue",
            slot="scripts",
            renderer="render_module_tag",
        )
        default_stems.register("template", "page")

        from polls import providers, signals  # noqa: F401, PLC0415
