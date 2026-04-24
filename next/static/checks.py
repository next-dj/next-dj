"""System checks for `NEXT_FRAMEWORK['DEFAULT_STATIC_BACKENDS']`.

The checks are hooked into `manage.py check` via the `@register`
decorator. All identifiers live in the `next.*` namespace to avoid
collisions with Django core checks.

Registered identifiers.

- `next.W030`. Warning raised when `DEFAULT_STATIC_BACKENDS` is empty.
  The framework falls back to the built-in staticfiles backend.
- `next.E036`. Error raised when a backend dotted path fails to import.
- `next.E037`. Error raised when the resolved class is not a
  `StaticBackend` subclass.
- `next.E038`. Error raised when the same `BACKEND` entry appears more
  than once in the configuration list.
- `next.W031`. Warning raised when `OPTIONS['css_tag']` or
  `OPTIONS['js_tag']` does not contain the `{url}` placeholder.
- `next.W042`. Warning raised when `JS_CONTEXT_SERIALIZER` is set but does
  not resolve to a class implementing the `JsContextSerializer` protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)

from next.conf import import_class_cached, next_framework_settings

from .backends import StaticBackend


if TYPE_CHECKING:
    from collections.abc import Iterable


def _validate_tag_template(
    tag_name: str,
    value: object,
    backend_index: int,
) -> CheckMessage | None:
    if not isinstance(value, str):
        return None
    if "{url}" not in value:
        return DjangoWarning(
            (
                f"OPTIONS[{tag_name!r}] in DEFAULT_STATIC_BACKENDS[{backend_index}] "
                "does not contain the '{url}' placeholder. Rendered tags will "
                "not include the asset URL."
            ),
            obj=settings,
            id="next.W031",
        )
    return None


def _check_single_backend(
    config: object,
    index: int,
    seen: set[str],
) -> Iterable[CheckMessage]:
    messages: list[CheckMessage] = []
    if not isinstance(config, dict):
        messages.append(
            Error(
                f"DEFAULT_STATIC_BACKENDS[{index}] must be a dict, got "
                f"{type(config).__name__!r}.",
                obj=settings,
                id="next.E037",
            )
        )
        return messages
    backend_path = config.get("BACKEND", "next.static.StaticFilesBackend")
    if not isinstance(backend_path, str):
        messages.append(
            Error(
                f"DEFAULT_STATIC_BACKENDS[{index}]['BACKEND'] must be a dotted "
                f"string, got {type(backend_path).__name__!r}.",
                obj=settings,
                id="next.E037",
            )
        )
        return messages
    if backend_path in seen:
        messages.append(
            Error(
                f"DEFAULT_STATIC_BACKENDS has duplicate BACKEND entry "
                f"{backend_path!r}.",
                obj=settings,
                id="next.E038",
            )
        )
        return messages
    seen.add(backend_path)
    try:
        backend_class = import_class_cached(backend_path)
    except ImportError as e:
        messages.append(
            Error(
                f"Cannot import static backend {backend_path!r}: {e}",
                obj=settings,
                id="next.E036",
            )
        )
        return messages
    if not isinstance(backend_class, type) or not issubclass(
        backend_class, StaticBackend
    ):
        messages.append(
            Error(
                f"Static backend {backend_path!r} is not a StaticBackend subclass.",
                obj=settings,
                id="next.E037",
            )
        )
        return messages
    options: Any = config.get("OPTIONS") or {}
    if isinstance(options, dict):
        for tag_name in ("css_tag", "js_tag"):
            if tag_name not in options:
                continue
            message = _validate_tag_template(tag_name, options[tag_name], index)
            if message is not None:
                messages.append(message)
    return messages


@register(Tags.compatibility)
def check_static_backends(
    app_configs: object,  # noqa: ARG001
    **_kwargs: object,
) -> list[CheckMessage]:
    """Validate the structure of `NEXT_FRAMEWORK['DEFAULT_STATIC_BACKENDS']`."""
    messages: list[CheckMessage] = []
    try:
        configs = next_framework_settings.DEFAULT_STATIC_BACKENDS
    except (AttributeError, ImportError) as e:  # pragma: no cover
        return [
            Error(
                f"Unable to read DEFAULT_STATIC_BACKENDS: {e}",
                obj=settings,
                id="next.E036",
            )
        ]

    if not isinstance(configs, list) or len(configs) == 0:
        messages.append(
            DjangoWarning(
                "NEXT_FRAMEWORK['DEFAULT_STATIC_BACKENDS'] is empty. The "
                "framework falls back to next.static.StaticFilesBackend.",
                obj=settings,
                id="next.W030",
            )
        )
        return messages

    seen: set[str] = set()
    for index, config in enumerate(configs):
        messages.extend(_check_single_backend(config, index, seen))
    return messages


def _w042(message: str) -> CheckMessage:
    """Return a next.W042 warning tied to settings as the object."""
    return DjangoWarning(message, obj=settings, id="next.W042")


@register(Tags.compatibility)
def check_js_context_serializer(  # noqa: PLR0911
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Validate that `JS_CONTEXT_SERIALIZER` resolves to a protocol implementation."""
    raw_framework = getattr(settings, "NEXT_FRAMEWORK", {}) or {}
    if not isinstance(raw_framework, dict):
        return []
    path = raw_framework.get("JS_CONTEXT_SERIALIZER")
    if path is None or path == "":
        return []
    if not isinstance(path, str):
        return [
            _w042(
                f"NEXT_FRAMEWORK['JS_CONTEXT_SERIALIZER'] must be a dotted path "
                f"string, got {type(path).__name__!r}."
            )
        ]
    try:
        cls: Any = import_class_cached(path)
    except ImportError as e:
        return [_w042(f"Cannot import JS_CONTEXT_SERIALIZER {path!r}: {e}")]
    if not isinstance(cls, type):
        return [_w042(f"JS_CONTEXT_SERIALIZER {path!r} is not a class.")]
    try:
        instance = cls()
    except (TypeError, ImportError) as e:
        return [_w042(f"JS_CONTEXT_SERIALIZER {path!r} cannot be instantiated: {e}")]
    from .serializers import JsContextSerializer  # noqa: PLC0415

    if not isinstance(instance, JsContextSerializer):
        return [
            _w042(
                f"JS_CONTEXT_SERIALIZER {path!r} does not implement the "
                "JsContextSerializer protocol (needs a `dumps(value) -> str` method)."
            )
        ]
    return []
