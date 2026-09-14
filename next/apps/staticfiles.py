"""Register next-dj's static files finder and built-in asset kinds."""

from __future__ import annotations

from django.conf import settings
from django.core.signals import setting_changed

from next.static.defaults import register_defaults


_FINDER_PATH = "next.static.NextStaticFilesFinder"


def install() -> None:
    """Wire the staticfiles finder and register the framework's built-in asset kinds.

    Both steps are idempotent, so a settings reload or a re-entrant `ready` is safe.
    """
    configured = list(getattr(settings, "STATICFILES_FINDERS", []))
    if _FINDER_PATH not in configured:
        settings.STATICFILES_FINDERS = [*configured, _FINDER_PATH]
    register_defaults()


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Re-add the finder when an override replaces the configured list."""
    if setting == "STATICFILES_FINDERS":
        install()


setting_changed.connect(_on_setting_changed)


__all__ = ["install"]
