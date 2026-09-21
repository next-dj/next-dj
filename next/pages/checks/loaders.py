"""System check for every `NEXT_FRAMEWORK['TEMPLATE_LOADERS']` entry.

The ids are `next.E042` for an entry that is no dotted path, `next.E043` for one that
cannot be imported, and `next.E089` for a class that is no `TemplateLoader`.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import CheckMessage, Error, register

from next.checks import NEXT
from next.conf import import_class_cached, next_framework_settings
from next.pages.loaders import TemplateLoader


@register(NEXT)
def check_template_loaders(*args, **kwargs) -> list[CheckMessage]:
    """Validate every `NEXT_FRAMEWORK['TEMPLATE_LOADERS']` entry."""
    try:
        configured = next_framework_settings.TEMPLATE_LOADERS
    except (AttributeError, ImportError):  # pragma: no cover
        return []

    messages: list[CheckMessage] = []
    for index, entry in enumerate(configured):
        if not isinstance(entry, str):
            messages.append(
                Error(
                    f"NEXT_FRAMEWORK['TEMPLATE_LOADERS'][{index}] must be a dotted "
                    f"path string, got {type(entry).__name__!r}.",
                    obj=settings,
                    id="next.E042",
                )
            )
            continue
        try:
            cls = import_class_cached(entry)
        except ImportError as exc:
            messages.append(
                Error(
                    f"NEXT_FRAMEWORK['TEMPLATE_LOADERS'][{index}]={entry!r} "
                    f"cannot be imported: {exc}.",
                    obj=settings,
                    id="next.E043",
                )
            )
            continue
        if not isinstance(cls, type) or not issubclass(cls, TemplateLoader):
            messages.append(
                Error(
                    f"NEXT_FRAMEWORK['TEMPLATE_LOADERS'][{index}]={entry!r} is "
                    "not a TemplateLoader subclass.",
                    obj=settings,
                    id="next.E089",
                )
            )
    return messages


__all__ = ["check_template_loaders"]
