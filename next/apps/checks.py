"""System checks for next-dj template engine wiring.

Registered identifiers.

- `next.W062`. Warning raised when no `DjangoTemplates` engine is
  configured in `TEMPLATES`. The next-dj `{% %}` tags install only into
  that backend, so they are unavailable without it.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Tags,
    Warning as DjangoWarning,
    register,
)

from .templates import _DJANGO_BACKEND


@register(Tags.templates)
def check_django_templates_backend_present(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when no DjangoTemplates engine carries the next-dj tags."""
    engines = getattr(settings, "TEMPLATES", [])
    if any(engine.get("BACKEND") == _DJANGO_BACKEND for engine in engines):
        return []
    return [
        DjangoWarning(
            "next-dj template tags require a DjangoTemplates backend. "
            "None is configured, so the {% %} tags will be unavailable.",
            obj=settings,
            id="next.W062",
        ),
    ]


__all__ = ["check_django_templates_backend_present"]
