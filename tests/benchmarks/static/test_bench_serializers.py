"""Benchmarks for ``next.static.serializers``.

The serializer runs on every ``@context(serialize=True)`` value that lands
in ``window.Next.context``. These benches establish a baseline for:

- ``resolve_serializer()`` cold and warm cost (no imp cache by design);
- ``JsonJsContextSerializer.dumps`` on differently shaped payloads;
- the pydantic branch when the optional dependency is installed.
"""

from __future__ import annotations

import pytest

from next.static.serializers import (
    JsonJsContextSerializer,
    PydanticJsContextSerializer,
    resolve_serializer,
)


_SMALL_DICT: dict[str, int] = {"a": 1}
_WIDE_DICT: dict[str, int] = {f"k_{i}": i for i in range(20)}
_NESTED_DICT: dict[str, object] = {
    "a": {"b": {"c": {"d": list(range(10))}}},
    "meta": {"page": "home", "ids": list(range(20))},
}


class TestBenchResolveSerializer:
    @pytest.mark.benchmark(group="static.serializers")
    def test_resolve_default(self, benchmark) -> None:
        """``JS_CONTEXT_SERIALIZER`` unset — should return a cached default."""
        benchmark(resolve_serializer)


class TestBenchJsonJsContextSerializer:
    @pytest.mark.benchmark(group="static.serializers")
    def test_dumps_small_dict(self, benchmark) -> None:
        _ = JsonJsContextSerializer()
        benchmark(_.dumps, _SMALL_DICT)

    @pytest.mark.benchmark(group="static.serializers")
    def test_dumps_wide_dict(self, benchmark) -> None:
        _ = JsonJsContextSerializer()
        benchmark(_.dumps, _WIDE_DICT)

    @pytest.mark.benchmark(group="static.serializers")
    def test_dumps_nested_dict(self, benchmark) -> None:
        _ = JsonJsContextSerializer()
        benchmark(_.dumps, _NESTED_DICT)


class TestBenchPydanticJsContextSerializer:
    @pytest.mark.benchmark(group="static.serializers")
    def test_dumps_model(self, benchmark) -> None:
        """``PydanticJsContextSerializer`` wraps a small ``BaseModel`` instance."""
        pydantic = pytest.importorskip("pydantic")

        class Payload(pydantic.BaseModel):
            slug: str
            url: str
            hits: int

        _ = PydanticJsContextSerializer()
        payload = Payload(slug="hello", url="https://example.com/", hits=42)
        benchmark(_.dumps, payload)
