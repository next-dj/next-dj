from django.apps import AppConfig

from next.partial import register_patch_op
from next.static import default_kinds

from .exporter import seed_series


METRIC_PULSE_OP = "metric-pulse"


class ObsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "obs"
    label = "obs"

    def ready(self) -> None:
        """Register the `jsx` kind and the patch verb, seed the Prometheus series."""
        default_kinds.register(
            "jsx", extension=".jsx", slot="scripts", renderer="render_babel_script_tag"
        )
        register_patch_op(METRIC_PULSE_OP)
        seed_series()
