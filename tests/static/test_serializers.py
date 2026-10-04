from __future__ import annotations

import datetime as dt
import json

import pytest
from django.test import override_settings

from next.static import StaticCollector
from next.static.serializers import (
    JsContextSerializer,
    JsonJsContextSerializer,
    PydanticJsContextSerializer,
    resolve_serializer,
)


class TestJsonJsContextSerializer:
    """The default serializer encodes plain values, dates and pydantic models."""

    def test_dumps_plain_dict(self) -> None:
        payload = JsonJsContextSerializer().dumps({"a": 1, "b": [1, 2]})
        assert json.loads(payload) == {"a": 1, "b": [1, 2]}

    def test_dumps_datetime(self) -> None:
        value = dt.datetime(2024, 1, 1, 12, 30, tzinfo=dt.UTC)
        payload = JsonJsContextSerializer().dumps({"when": value})
        assert json.loads(payload) == {"when": "2024-01-01T12:30:00Z"}

    def test_raises_on_unsupported_type(self) -> None:
        with pytest.raises(TypeError, match="not JSON serializable"):
            JsonJsContextSerializer().dumps({"thing": object()})


class TestPydanticJsContextSerializer:
    """Pydantic serializer unwraps `BaseModel` instances via `model_dump`."""

    def test_dumps_pydantic_model(self) -> None:
        pydantic = pytest.importorskip("pydantic")

        class Product(pydantic.BaseModel):
            name: str
            price: float

        serializer = PydanticJsContextSerializer()
        payload = serializer.dumps({"product": Product(name="Book", price=9.99)})
        assert json.loads(payload) == {"product": {"name": "Book", "price": 9.99}}
        assert " " not in payload.split(":")[0]

    def test_falls_back_to_parent_encoder_for_unknown_types(self) -> None:
        pytest.importorskip("pydantic")
        serializer = PydanticJsContextSerializer()
        with pytest.raises(TypeError, match="not JSON serializable"):
            serializer.dumps({"thing": object()})

    def test_the_encoder_class_is_built_once(self) -> None:
        """A render resolves the serializer afresh, so the class cannot be per call."""
        pytest.importorskip("pydantic")

        first = PydanticJsContextSerializer()
        second = PydanticJsContextSerializer()

        assert first._encoder is second._encoder

    def test_raises_when_pydantic_missing(self, monkeypatch) -> None:
        monkeypatch.setattr("next.static.serializers.pydantic", None)
        with pytest.raises(ImportError, match="pydantic"):
            PydanticJsContextSerializer()


class TestResolveSerializer:
    """resolve_serializer picks up the configured class or falls back."""

    def test_falls_back_to_default_when_unset(self) -> None:
        assert isinstance(resolve_serializer(), JsonJsContextSerializer)

    @override_settings(
        NEXT_FRAMEWORK={
            "JS_CONTEXT_SERIALIZER": (
                "next.static.serializers.PydanticJsContextSerializer"
            )
        }
    )
    def test_reads_dotted_path_from_settings(self) -> None:
        pytest.importorskip("pydantic")
        assert isinstance(resolve_serializer(), PydanticJsContextSerializer)

    @pytest.mark.parametrize(
        "path",
        [
            "tests.static.test_serializers._BadCls",
            "tests.static.test_serializers._Raising",
            "tests.static.test_serializers._Missing",
        ],
        ids=["no_protocol", "raises", "missing"],
    )
    def test_a_broken_serializer_gives_way_to_json_logged_once(
        self, caplog, path
    ) -> None:
        with override_settings(NEXT_FRAMEWORK={"JS_CONTEXT_SERIALIZER": path}):
            answers = [resolve_serializer() for _ in range(2)]
        assert all(isinstance(answer, JsonJsContextSerializer) for answer in answers)
        [record] = [r for r in caplog.records if r.name == "next.static.serializers"]
        assert path in record.getMessage()

    def test_a_broken_serializer_raises_under_debug(self) -> None:
        path = "tests.static.test_serializers._BadCls"
        with (
            override_settings(
                DEBUG=True, NEXT_FRAMEWORK={"JS_CONTEXT_SERIALIZER": path}
            ),
            pytest.raises(TypeError, match="JsContextSerializer") as raised,
        ):
            resolve_serializer()
        assert "JS_CONTEXT_SERIALIZER" in raised.value.__notes__[0]

    def test_one_instance_serves_every_render_until_a_reload(self) -> None:
        path = "next.static.serializers.JsonJsContextSerializer"
        with override_settings(NEXT_FRAMEWORK={"JS_CONTEXT_SERIALIZER": path}):
            first = resolve_serializer()
            assert resolve_serializer() is first
        with override_settings(NEXT_FRAMEWORK={"JS_CONTEXT_SERIALIZER": path}):
            assert resolve_serializer() is not first


class TestCollectorUsesSerializer:
    """StaticCollector.add_js_context routes through the configured serializer."""

    def test_default_path_accepts_datetime(self) -> None:
        collector = StaticCollector()
        collector.add_js_context("when", dt.datetime(2024, 1, 1, tzinfo=dt.UTC))
        assert "when" in collector.js_context()

    def test_rejects_unsupported_value_with_clear_error(self) -> None:
        collector = StaticCollector()
        with pytest.raises(TypeError, match="not serialisable"):
            collector.add_js_context("thing", object())

    def test_explicit_serializer_override(self) -> None:
        class TagSerializer:
            def dumps(self, value: object) -> str:
                return json.dumps({"tagged": value})

        collector = StaticCollector(js_serializer=TagSerializer())
        collector.add_js_context("a", 1)
        assert collector.js_context() == {"a": 1}


class _Raising:
    """A serializer whose constructor fails."""

    def __init__(self) -> None:
        msg = "no encoder"
        raise RuntimeError(msg)


class _BadCls:
    """Class that does not implement the JsContextSerializer protocol."""


class TestProtocolRuntimeCheck:
    """The protocol is runtime-checkable via isinstance."""

    def test_default_serializer_is_instance_of_protocol(self) -> None:
        assert isinstance(JsonJsContextSerializer(), JsContextSerializer)
