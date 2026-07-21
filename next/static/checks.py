"""System checks for the static subsystem.

All identifiers live in the `next.*` namespace to avoid collisions with
Django core checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT
from next.components.context import iter_serialized_component_context_keys
from next.conf import import_class_cached, next_framework_settings
from next.pages.manager import iter_serialized_page_context_keys

from .assets import default_kinds
from .backends import StaticBackend
from .scripts import RESERVED_PAYLOAD_CONDITIONS, RESERVED_PAYLOAD_KEYS
from .serializers import JsContextSerializer


if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


def _validate_tag_template(
    tag_name: str, value: object, backend_index: int
) -> CheckMessage | None:
    if not isinstance(value, str):
        return None
    if "{url}" not in value:
        return DjangoWarning(
            (
                f"OPTIONS[{tag_name!r}] in STATIC_BACKENDS[{backend_index}] "
                "does not contain the '{url}' placeholder. Rendered tags will "
                "not include the asset URL."
            ),
            obj=settings,
            id="next.W031",
        )
    return None


def _check_single_backend(
    config: object, index: int, seen: set[str]
) -> Iterable[CheckMessage]:
    messages: list[CheckMessage] = []
    if not isinstance(config, dict):
        messages.append(
            Error(
                f"STATIC_BACKENDS[{index}] must be a dict, got "
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
                f"STATIC_BACKENDS[{index}]['BACKEND'] must be a dotted "
                f"string, got {type(backend_path).__name__!r}.",
                obj=settings,
                id="next.E037",
            )
        )
        return messages
    if backend_path in seen:
        messages.append(
            Error(
                f"STATIC_BACKENDS has duplicate BACKEND entry {backend_path!r}.",
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


@register(NEXT)
def check_static_backends(**kwargs) -> list[CheckMessage]:
    """Validate the structure of `NEXT_FRAMEWORK['STATIC_BACKENDS']`."""
    messages: list[CheckMessage] = []
    try:
        configs = next_framework_settings.STATIC_BACKENDS
    except (AttributeError, ImportError) as e:  # pragma: no cover
        return [
            Error(f"Unable to read STATIC_BACKENDS: {e}", obj=settings, id="next.E036")
        ]

    if not isinstance(configs, list) or len(configs) == 0:
        messages.append(
            DjangoWarning(
                "NEXT_FRAMEWORK['STATIC_BACKENDS'] is empty. The "
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


@register(NEXT)
def check_asset_kinds_are_loadable(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a registered kind the partial runtime cannot insert."""
    return [
        DjangoWarning(
            f"Asset kind {kind!r} is registered with renderer "
            f"{default_kinds.renderer(kind)!r}, which carries no client "
            "insertion verb. Assets of this kind reach the browser only on a "
            "full page render, never through a patch envelope. Register the "
            "kind with render_link_tag, render_script_tag, or render_module_tag.",
            obj=settings,
            id="next.W074",
        )
        for kind in default_kinds.kinds()
        if default_kinds.load(kind) is None
    ]


def _reserved_key_warning(origin: str, source_path: Path, key: str) -> CheckMessage:
    """Return the `next.W075` warning for one reserved-key collision."""
    return DjangoWarning(
        f"{origin} context key {key!r} in {source_path} is reserved for the "
        f"next.min.js init payload. The framework writes {key} "
        f"{RESERVED_PAYLOAD_CONDITIONS[key]} and its value wins there. A render "
        "that leaves the key out keeps the registered value instead, so "
        "window.Next.context differs between environments. Rename the key.",
        obj=str(source_path),
        id="next.W075",
    )


@register(NEXT)
def check_reserved_js_context_keys(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a page or component context key the init payload reserves.

    Pages and components feed the same init payload, so both registries are
    walked against the reserved namespace.
    """
    sources = (
        ("Page", iter_serialized_page_context_keys()),
        ("Component", iter_serialized_component_context_keys()),
    )
    return [
        _reserved_key_warning(origin, source_path, key)
        for origin, entries in sources
        for source_path, key in entries
        if key in RESERVED_PAYLOAD_KEYS
    ]


def _w042(message: str) -> CheckMessage:
    """Return a next.W042 warning tied to settings as the object."""
    return DjangoWarning(message, obj=settings, id="next.W042")


@register(NEXT)
def check_js_context_serializer(*args, **kwargs) -> list[CheckMessage]:
    """Validate that `JS_CONTEXT_SERIALIZER` resolves to a protocol implementation."""
    message = _js_context_serializer_message()
    return [message] if message is not None else []


def _js_context_serializer_message() -> CheckMessage | None:
    """Return the single configuration warning for `JS_CONTEXT_SERIALIZER`.

    An unset option or a non-dict `NEXT_FRAMEWORK` is healthy and returns
    None, so the registered check stays a thin list wrapper.
    """
    raw_framework = getattr(settings, "NEXT_FRAMEWORK", {}) or {}
    if not isinstance(raw_framework, dict):
        return None
    path = raw_framework.get("JS_CONTEXT_SERIALIZER")
    if path is None or path == "":
        return None
    if not isinstance(path, str):
        return _w042(
            f"NEXT_FRAMEWORK['JS_CONTEXT_SERIALIZER'] must be a dotted path "
            f"string, got {type(path).__name__!r}."
        )
    return _js_context_serializer_instance_message(path)


def _js_context_serializer_instance_message(path: str) -> CheckMessage | None:
    """Import and instantiate the configured serializer, reporting any failure."""
    try:
        cls: Any = import_class_cached(path)
    except ImportError as e:
        return _w042(f"Cannot import JS_CONTEXT_SERIALIZER {path!r}: {e}")
    if not isinstance(cls, type):
        return _w042(f"JS_CONTEXT_SERIALIZER {path!r} is not a class.")
    try:
        instance = cls()
    except (TypeError, ImportError) as e:
        return _w042(f"JS_CONTEXT_SERIALIZER {path!r} cannot be instantiated: {e}")
    if not isinstance(instance, JsContextSerializer):
        return _w042(
            f"JS_CONTEXT_SERIALIZER {path!r} does not implement the "
            "JsContextSerializer protocol (needs a `dumps(value) -> str` method)."
        )
    return None
