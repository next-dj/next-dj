"""System checks for the context processors a page render runs.

The ids are `next.E019` for a `TEMPLATES` entry without Django's request processor and
`next.E040` for a configured processor that takes no `request`.
"""

from __future__ import annotations

import inspect
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register
from django.utils.module_loading import import_string

from next.checks import NEXT
from next.conf import next_framework_settings


REQUEST_CONTEXT_PROCESSOR = "django.template.context_processors.request"


@register(Tags.templates, NEXT)
def check_request_in_context(*args, **kwargs) -> list[CheckMessage]:
    """Ensure `request` is in the template context (required for `{% form %}`)."""
    # Through the registry, so an `AppConfig` path in `INSTALLED_APPS` counts too.
    if not apps.is_installed("next"):
        return []

    errors: list[CheckMessage] = []
    templates = getattr(settings, "TEMPLATES", [])

    for i, config in enumerate(templates):
        if not isinstance(config, dict):
            continue
        options = config.get("OPTIONS", {})
        processors = options.get("context_processors", [])
        if REQUEST_CONTEXT_PROCESSOR not in processors:
            msg = (
                f"TEMPLATES[{i}]: 'request' must be in template context "
                "when using next (required for {% form %} and CSRF). Add "
                "'django.template.context_processors.request' to "
                "OPTIONS.context_processors."
            )
            errors.append(Error(msg, obj=settings, id="next.E019"))
    return errors


@register(Tags.templates, NEXT)
def check_context_processor_signature(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a configured context processor has no `request` parameter."""
    errors: list[CheckMessage] = []
    for backend_index, backend in _iter_page_backend_configs():
        processors = backend.get("OPTIONS", {}).get("context_processors") or []
        for processor_index, path in enumerate(processors):
            if not isinstance(path, str):
                continue
            loc = (
                f"NEXT_FRAMEWORK['PAGE_BACKENDS'][{backend_index}]"
                f".OPTIONS.context_processors[{processor_index}]"
            )
            message = _check_processor_request_parameter(path, loc)
            if message is not None:
                errors.append(message)
    return errors


def _iter_page_backend_configs() -> list[tuple[int, dict[str, Any]]]:
    """Return the indexed page backend dicts, read through the settings façade.

    The façade is what every reader of the merged settings sees, so a project
    naming no backend is checked on the defaults it actually runs.
    """
    configured = next_framework_settings.PAGE_BACKENDS
    if not isinstance(configured, list):
        return []
    return [
        (index, backend)
        for index, backend in enumerate(configured)
        if isinstance(backend, dict)
    ]


def _check_processor_request_parameter(
    processor_path: str, location: str
) -> CheckMessage | None:
    """Return an error when the callable at `processor_path` lacks `request`."""
    try:
        callable_obj = import_string(processor_path)
    except ImportError:
        return None
    if not callable(callable_obj):
        return None
    try:
        sig = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return None
    if "request" in sig.parameters:
        return None
    return Error(
        f"{location} points at {processor_path!r} which does not accept a "
        "'request' parameter. Context processors must accept request.",
        obj=settings,
        id="next.E040",
    )


__all__ = [
    "REQUEST_CONTEXT_PROCESSOR",
    "check_context_processor_signature",
    "check_request_in_context",
]
