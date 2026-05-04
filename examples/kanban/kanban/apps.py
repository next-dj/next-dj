from django.apps import AppConfig


class KanbanConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "kanban"

    def ready(self) -> None:
        """Register the .jsx asset kind and import action handlers at startup."""
        from next.static import default_kinds  # noqa: PLC0415

        default_kinds.register(
            "jsx",
            extension=".jsx",
            slot="scripts",
            renderer="render_babel_script_tag",
        )

        from kanban import actions, providers  # noqa: F401, PLC0415
