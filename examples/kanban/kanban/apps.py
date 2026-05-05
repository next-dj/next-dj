from django.apps import AppConfig

from next.static import default_kinds
from next.static.discovery import default_stems


class KanbanConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "kanban"

    def ready(self) -> None:
        """Register JSX kind, page stem, and connect signal handlers."""
        default_kinds.register(
            "jsx",
            extension=".jsx",
            slot="scripts",
            renderer="render_module_tag",
        )
        default_stems.register("template", "page")

        from kanban import providers, signals  # noqa: F401, PLC0415
