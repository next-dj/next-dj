"""Pluggable JS-context serializers for `@context(serialize=True)` values.

`StaticCollector.add_js_context` delegates value encoding to a
`JsContextSerializer`. The default implementation uses
`DjangoJSONEncoder`, which handles the same set of types that the
framework has always accepted. Applications that want to serialise
pydantic models, msgspec structs, or any other type can point the
`JS_CONTEXT_SERIALIZER` option at a class that implements the
protocol.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from django.core.serializers.json import DjangoJSONEncoder


@runtime_checkable
class JsContextSerializer(Protocol):
    """Encode values destined for `window.Next.context`.

    Implementations turn Python values into JSON text. The contract is
    deliberately narrow so that custom types can travel to the client
    without bolt-on Django encoder extensions.
    """

    def dumps(self, value: Any) -> str:  # noqa: ANN401
        """Return a JSON string for `value`."""
        raise NotImplementedError


class JsonJsContextSerializer:
    """Serialise values with Django's `DjangoJSONEncoder`.

    This is the process-wide default. It mirrors the behaviour built
    into the collector before serializers became pluggable. The output
    uses compact separators so the inline init payload stays small.
    """

    def dumps(self, value: Any) -> str:  # noqa: ANN401
        """Return a compact JSON string produced by `json.dumps`."""
        return json.dumps(value, cls=DjangoJSONEncoder, separators=(",", ":"))


class PydanticJsContextSerializer:
    """Serialise values through pydantic model dump when available.

    Unknown types fall through to `DjangoJSONEncoder`, so lists and
    dicts containing mixed pydantic and plain values still serialise
    without a second code path.
    """

    def __init__(self) -> None:
        """Import pydantic lazily so tests without it keep working."""
        try:
            import pydantic  # noqa: PLC0415
        except ImportError as e:
            msg = (
                "PydanticJsContextSerializer requires the pydantic package. "
                "Install it or switch JS_CONTEXT_SERIALIZER to another class."
            )
            raise ImportError(msg) from e
        self._pydantic = pydantic

    def dumps(self, value: Any) -> str:  # noqa: ANN401
        """Return a compact JSON string with pydantic models unwrapped."""
        return json.dumps(value, cls=_PydanticAwareEncoder, separators=(",", ":"))


class _PydanticAwareEncoder(DjangoJSONEncoder):
    """Fallback encoder that unwraps pydantic `BaseModel` instances."""

    def default(self, o: Any) -> Any:  # noqa: ANN401
        """Dump `BaseModel` subclasses via `model_dump` before deferring."""
        import pydantic  # noqa: PLC0415

        if isinstance(o, pydantic.BaseModel):
            return o.model_dump(mode="json")
        return super().default(o)


_default_serializer: JsContextSerializer = JsonJsContextSerializer()


def resolve_serializer() -> JsContextSerializer:
    """Return the configured serializer or the process-wide default.

    The resolver reads `NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]` on
    every call. Returning a fresh instance each time keeps the hot path
    free of caching edge cases during test overrides.
    """
    from next.conf import import_class_cached, next_framework_settings  # noqa: PLC0415

    path = getattr(next_framework_settings, "JS_CONTEXT_SERIALIZER", None)
    if not path:
        return _default_serializer
    cls = import_class_cached(str(path))
    instance = cls()
    if not isinstance(instance, JsContextSerializer):
        msg = f"{path!r} does not implement JsContextSerializer"
        raise TypeError(msg)
    return instance


__all__ = [
    "JsContextSerializer",
    "JsonJsContextSerializer",
    "PydanticJsContextSerializer",
    "resolve_serializer",
]
