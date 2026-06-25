from django.apps import AppConfig

from next.partial import register_patch_op
from next.static import default_kinds


METRIC_PULSE_OP = "metric-pulse"


class ObsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "obs"

    def ready(self) -> None:
        """Register asset kinds, the custom patch verb, and signal receivers."""
        default_kinds.register(
            "jsx",
            extension=".jsx",
            slot="scripts",
            renderer="render_babel_script_tag",
        )
        register_patch_op(METRIC_PULSE_OP)
        from obs import receivers  # noqa: F401, PLC0415
