"""Pluggable JS-context serializers for `@context(serialize=True)` values.

`StaticCollector.add_js_context` delegates encoding to a `JsContextSerializer`,
pluggable via `JS_CONTEXT_SERIALIZER` for pydantic, msgspec, or another type.
"""

from __future__ import annotations

import functools
import json
import logging
from typing import TYPE_CHECKING, Protocol, override, runtime_checkable

from django.core.serializers.json import DjangoJSONEncoder

from next.conf import import_class_cached, next_framework_settings
from next.conf.signals import settings_reloaded
from next.diagnostics import FailureLog


if TYPE_CHECKING:
    from types import ModuleType

pydantic: ModuleType | None
try:
    import pydantic
except ImportError:  # pragma: no cover - pydantic is installed in CI
    # pydantic is an optional dependency. Its absence is reported when the
    # pydantic serializer is constructed, never at module import.
    pydantic = None


@runtime_checkable
class JsContextSerializer(Protocol):
    """Encode values destined for `window.Next.context`.

    The contract stays narrow, so a custom type needs no Django encoder extension.
    """

    def dumps(self, value: object) -> str:
        """Return a JSON string for `value`."""
        raise NotImplementedError


class JsonJsContextSerializer:
    """Serialise values with Django's `DjangoJSONEncoder`.

    It is the process-wide default, and compact separators keep the init payload small.
    """

    def dumps(self, value: object) -> str:
        """Return a compact JSON string produced by `json.dumps`."""
        return json.dumps(value, cls=DjangoJSONEncoder, separators=(",", ":"))


class PydanticJsContextSerializer:
    """Serialise values through pydantic model dump when available.

    Unknown types fall through to `DjangoJSONEncoder`, so lists and dicts containing
    mixed pydantic and plain values still serialise without a second code path.
    """

    def __init__(self) -> None:
        """Require the optional pydantic package, reporting its absence."""
        if pydantic is None:
            msg = (
                "PydanticJsContextSerializer requires the pydantic package. "
                "Install it or switch JS_CONTEXT_SERIALIZER to another class."
            )
            raise ImportError(msg)
        self._encoder = _pydantic_encoder(pydantic)

    def dumps(self, value: object) -> str:
        """Return a compact JSON string with pydantic models unwrapped."""
        return json.dumps(value, cls=self._encoder, separators=(",", ":"))


@functools.cache
def _pydantic_encoder(module: ModuleType) -> type[DjangoJSONEncoder]:
    """Return the encoder unwrapping `BaseModel` of the validated module.

    Built once per module rather than per serializer, because defining a class
    costs a namespace and an MRO while the encoder it yields holds no state.
    """
    base_model = module.BaseModel

    class _PydanticAwareEncoder(DjangoJSONEncoder):
        """Fallback encoder that unwraps pydantic `BaseModel` instances."""

        @override
        def default(self, o: object) -> object:
            """Dump `BaseModel` subclasses via `model_dump` before deferring."""
            if isinstance(o, base_model):
                return o.model_dump(mode="json")
            return super().default(o)

    return _PydanticAwareEncoder


logger = logging.getLogger(__name__)

_failures = FailureLog(logger)

_default_serializer: JsContextSerializer = JsonJsContextSerializer()


class _Configured:
    """The serializer `JS_CONTEXT_SERIALIZER` names, built once per dotted path."""

    def __init__(self) -> None:
        """Hold nothing until the first render asks."""
        self.held: tuple[str, JsContextSerializer] | None = None

    def forget(self, **kwargs: object) -> None:
        """Drop the held serializer, so a settings reload builds the new one."""
        self.held = None


_configured = _Configured()
settings_reloaded.connect(_configured.forget)


def _instantiate(path: str) -> JsContextSerializer:
    """Import and instantiate the serializer `path` names, refusing a non-serializer."""
    instance = import_class_cached(path)()
    if not isinstance(instance, JsContextSerializer):
        msg = f"{path!r} does not implement JsContextSerializer"
        raise TypeError(msg)
    return instance


def _build_serializer(path: str) -> JsContextSerializer:
    """Import and instantiate the serializer `path` names, the default on a failure.

    A broken serializer would fail every render, so the JSON default replaces it. The
    error is raised under `DEBUG` and logged once otherwise, and `next.W079` to
    `next.W082` report the cause.
    """
    try:
        instance = _instantiate(path)
    except Exception as exc:  # noqa: BLE001 - a project serializer may raise anything
        _failures.contain(
            exc,
            path,
            "JS_CONTEXT_SERIALIZER %r cannot serialize, so the JSON serializer "
            "is used instead. Point it at a class whose instances have a "
            "dumps(value) -> str method.",
            path,
        )
        return _default_serializer
    return instance


def resolve_serializer() -> JsContextSerializer:
    """Return the configured serializer or the process-wide default.

    The dotted path is read on every call, so a settings override takes effect from
    the call that follows it, while the instance is built once per path.
    """
    path = getattr(next_framework_settings, "JS_CONTEXT_SERIALIZER", None)
    if not path:
        return _default_serializer
    path = str(path)
    held = _configured.held
    if held is not None and held[0] == path:
        return held[1]
    serializer = _build_serializer(path)
    _configured.held = (path, serializer)
    return serializer


__all__ = [
    "JsContextSerializer",
    "JsonJsContextSerializer",
    "PydanticJsContextSerializer",
    "resolve_serializer",
]
