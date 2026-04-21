"""Register next-dj's static files finder in Django's `STATICFILES_FINDERS`."""

from __future__ import annotations

from django.conf import settings


_FINDER_PATH = "next.static.NextStaticFilesFinder"


def install() -> None:
    """Append `NextStaticFilesFinder` to `STATICFILES_FINDERS` if missing."""
    configured = list(getattr(settings, "STATICFILES_FINDERS", []))
    if _FINDER_PATH not in configured:
        configured.append(_FINDER_PATH)
        settings.STATICFILES_FINDERS = configured


__all__ = ["install"]
