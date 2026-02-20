"""Django app configuration for next-dj framework."""

from django.apps import AppConfig
from django.conf import settings


class NextFrameworkConfig(AppConfig):
    """Configuration class for the next-dj Django framework app."""

    name = "next"
    verbose_name = "Next Django Framework"

    def ready(self) -> None:
        """Patch StatReloader, register pages dirs, add form builtins."""
        # Deferred imports to avoid circular deps (next.urls/next.utils) and
        # because autoreload is only needed when wiring the reloader.
        from django.utils import autoreload  # noqa: PLC0415
        from django.utils.autoreload import autoreload_started  # noqa: PLC0415

        from next.urls import get_pages_directories_for_watch  # noqa: PLC0415
        from next.utils import NextStatReloader  # noqa: PLC0415

        autoreload.StatReloader = NextStatReloader  # type: ignore[misc]

        builtins = list(settings.TEMPLATES[0].get("OPTIONS", {}).get("builtins", []))
        if "next.templatetags.forms" not in builtins:
            builtins.append("next.templatetags.forms")
            settings.TEMPLATES[0].setdefault("OPTIONS", {})["builtins"] = builtins

        def watch_pages(sender: object, **_: object) -> None:
            for p in get_pages_directories_for_watch():
                sender.watch_dir(p, "**/page.py")  # type: ignore[attr-defined]

        autoreload_started.connect(watch_pages)
