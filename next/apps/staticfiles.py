"""Register next-dj's static files finders and built-in asset kinds."""

from __future__ import annotations

from django.conf import settings
from django.core.signals import setting_changed

from next.static.defaults import register_defaults


_FINDER_PATH = "next.static.NextStaticFilesFinder"
_APP_DIRECTORIES_PATH = "django.contrib.staticfiles.finders.AppDirectoriesFinder"
_NEXT_APP_DIRECTORIES_PATH = "next.static.NextAppDirectoriesFinder"
_FRAMEWORK_FINDERS = frozenset({_FINDER_PATH, _NEXT_APP_DIRECTORIES_PATH})


def install() -> None:
    """Wire the staticfiles finders and register the framework's built-in asset kinds.

    Both steps are idempotent, so a settings reload or a re-entrant `ready` is safe.
    A list naming the stock finder beside the framework one collapses to one entry.
    """
    configured: list[object] = []
    for path in getattr(settings, "STATICFILES_FINDERS", []):
        mapped = _NEXT_APP_DIRECTORIES_PATH if path == _APP_DIRECTORIES_PATH else path
        if mapped in _FRAMEWORK_FINDERS and mapped in configured:
            continue
        configured.append(mapped)
    if _FINDER_PATH not in configured:
        configured.append(_FINDER_PATH)
    settings.STATICFILES_FINDERS = configured
    register_defaults()


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Re-add the finders when an override replaces the configured list."""
    if setting == "STATICFILES_FINDERS":
        install()


setting_changed.connect(_on_setting_changed)


__all__ = ["install"]
