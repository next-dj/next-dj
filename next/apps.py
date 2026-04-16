"""Django app configuration for next-dj framework."""

from django.apps import AppConfig
from django.conf import settings


class NextFrameworkConfig(AppConfig):
    """Connect autoreload, template tag builtins, and filesystem watches."""

    name = "next"
    verbose_name = "Next Django Framework"

    def ready(self) -> None:
        """Replace StatReloader, register tag builtins, and attach directory watches."""
        # Deferred imports avoid circular imports between next.urls and next.server.
        from django.utils import autoreload  # noqa: PLC0415
        from django.utils.autoreload import autoreload_started  # noqa: PLC0415

        from next.server import (  # noqa: PLC0415
            NextStatReloader,
            iter_all_autoreload_watch_specs,
        )

        autoreload.StatReloader = NextStatReloader  # type: ignore[misc]

        builtins = list(settings.TEMPLATES[0].get("OPTIONS", {}).get("builtins", []))
        for mod in (
            "next.templatetags.forms",
            "next.templatetags.components",
            "next.templatetags.next_static",
        ):
            if mod not in builtins:
                builtins.append(mod)
        settings.TEMPLATES[0].setdefault("OPTIONS", {})["builtins"] = builtins

        def watch_next_filesystem(sender: object, **_: object) -> None:
            for path, glob in iter_all_autoreload_watch_specs():
                sender.watch_dir(path, glob)  # type: ignore[attr-defined]

        autoreload_started.connect(watch_next_filesystem)

        from next.components import components_manager  # noqa: PLC0415

        components_manager._ensure_backends()
        for backend in components_manager._backends:
            if hasattr(backend, "import_all_component_modules"):
                backend.import_all_component_modules()
