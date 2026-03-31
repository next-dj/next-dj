"""Django app configuration for next-dj framework."""

from django.apps import AppConfig
from django.conf import settings


class NextFrameworkConfig(AppConfig):
    """Wires autoreload, template tag builtins, and page directory watches."""

    name = "next"
    verbose_name = "Next Django Framework"

    def ready(self) -> None:
        """Swap ``StatReloader``, ensure tag builtins, watch pages trees."""
        # Deferred imports to avoid circular deps (next.urls/next.server) and
        # because autoreload is only needed when wiring the reloader.
        from django.utils import autoreload  # noqa: PLC0415
        from django.utils.autoreload import autoreload_started  # noqa: PLC0415

        from next.pages import get_pages_directories_for_watch  # noqa: PLC0415
        from next.server import NextStatReloader  # noqa: PLC0415

        autoreload.StatReloader = NextStatReloader  # type: ignore[misc]

        builtins = list(settings.TEMPLATES[0].get("OPTIONS", {}).get("builtins", []))
        for mod in ("next.templatetags.forms", "next.templatetags.components"):
            if mod not in builtins:
                builtins.append(mod)
        settings.TEMPLATES[0].setdefault("OPTIONS", {})["builtins"] = builtins

        def watch_pages(sender: object, **_: object) -> None:
            for p in get_pages_directories_for_watch():
                sender.watch_dir(p, "**/page.py")  # type: ignore[attr-defined]

        autoreload_started.connect(watch_pages)
