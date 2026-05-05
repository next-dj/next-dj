"""Register next-dj's static files finder and built-in asset kinds."""

from __future__ import annotations

from django.conf import settings

from next.static.defaults import register_defaults


_FINDER_PATH = "next.static.NextStaticFilesFinder"


def install() -> None:
    """Wire the staticfiles finder and register the built-in `css` and `js` kinds.

    Adds `NextStaticFilesFinder` to `STATICFILES_FINDERS` once and
    populates the public `KindRegistry` and `PlaceholderRegistry` with
    framework-shipped defaults through the same API user code uses.
    Both steps are idempotent so the function is safe under settings
    reloads or re-entrant `ready` calls.
    """
    configured = list(getattr(settings, "STATICFILES_FINDERS", []))
    if _FINDER_PATH not in configured:
        configured.append(_FINDER_PATH)
        settings.STATICFILES_FINDERS = configured
    register_defaults()


__all__ = ["install"]
