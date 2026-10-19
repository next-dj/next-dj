from django.apps import AppConfig

from next.static import default_kinds, default_stems


class PollsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "polls"
    label = "polls"

    def ready(self) -> None:
        """Register the Vue asset kind and the `page` stem for co-located templates."""
        default_kinds.register(
            "vue", extension=".vue", slot="scripts", renderer="render_module_tag"
        )
        default_stems.register("template", "page")
