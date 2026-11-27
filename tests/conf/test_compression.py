import pytest
from django.core.exceptions import ImproperlyConfigured

from next.conf.compression import (
    SHAPE,
    StreamOwner,
    TopicMode,
    compression_settings,
    read_settings,
)
from next.conf.defaults import DEFAULTS
from next.conf.shapes import Misfit, Shape, misfits
from next.testing import override_next_settings


class TestReadSettings:
    """`read_settings` answers every key, the default for an omitted or misfit one."""

    def test_the_defaults(self) -> None:
        current = read_settings({})
        assert current.enabled is True
        assert [
            (entry.path, entry.level, entry.window) for entry in current.backends
        ] == [
            ("next.compression.ZstdBackend", 3, 17),
            ("next.compression.BrotliBackend", 4, 18),
            ("next.compression.GzipBackend", 4, None),
        ]
        assert current.order == ("zstd", "br", "gzip")
        assert (current.min_size, current.padding) == (1024, 32)
        assert (current.streams, current.sse, current.sse_topics) == (
            "self",
            True,
            "off",
        )
        assert current.coalesce == 0.002
        assert current.respect_no_transform is True
        assert current.exclude is None
        assert "text/*" in current.content_types

    def test_the_shapes_cover_every_default_key(self) -> None:
        assert set(SHAPE.keys) == set(DEFAULTS["COMPRESSION"])

    def test_a_backend_entry_keeps_what_it_was_written_as(self) -> None:
        written = {"BACKEND": "next.compression.GzipBackend", "OPTIONS": {"LEVEL": 6}}
        bare = {"BACKEND": "next.compression.BrotliBackend"}
        gzip, brotli = read_settings({"BACKENDS": [written, bare]}).backends
        assert (gzip.path, gzip.level, gzip.window) == (written["BACKEND"], 6, None)
        assert gzip.config == written
        assert (brotli.level, brotli.window) == (None, None)

    def test_written_values_are_read(self) -> None:
        current = read_settings(
            {
                "ENABLED": False,
                "ORDER": ["gzip"],
                "MIN_SIZE": 0,
                "STREAMS": "middleware",
                "SSE_TOPICS": "reset",
                "COALESCE": "500ms",
                "EXCLUDE": "myapp.rules.exclude",
            }
        )
        assert current.enabled is False
        assert current.order == ("gzip",)
        assert current.min_size == 0
        assert current.streams is StreamOwner.MIDDLEWARE
        assert current.sse_topics is TopicMode.RESET
        assert current.coalesce == 0.5
        assert current.exclude == "myapp.rules.exclude"

    def test_a_misfit_raises_naming_every_value(self) -> None:
        with pytest.raises(ImproperlyConfigured) as raised:
            read_settings({"MIN_SIZE": -1, "ORDER": "gzip"})
        message = str(raised.value)
        assert "NEXT_FRAMEWORK['COMPRESSION']['ORDER'] must be" in message
        assert "NEXT_FRAMEWORK['COMPRESSION']['MIN_SIZE'] must be" in message
        assert "got -1." in message


@pytest.mark.parametrize(
    ("key", "value", "usable"),
    [
        ("ENABLED", 1, False),
        ("BACKENDS", ["a.B"], False),
        ("BACKENDS", [{"BACKEND": ""}], False),
        ("BACKENDS", ({"BACKEND": "a.B"},), True),
        ("BACKENDS", [{"BACKEND": "a.B", "OPTIONS": {"LEVEL": 1, "WINDOW": 20}}], True),
        ("BACKENDS", [{"BACKEND": "a.B", "OPTIONS": {"LEVEL": True}}], False),
        ("BACKENDS", [{"BACKEND": "a.B", "OPTIONS": {"SIZE": 1}}], False),
        ("BACKENDS", [{"BACKEND": "a.B", "OPTIONS": ()}], False),
        ("BACKENDS", [{"BACKEND": "a.B", "NAME": "b"}], False),
        ("ORDER", "gzip", False),
        ("MIN_SIZE", 1.5, False),
        ("MIN_SIZE", True, False),
        ("CONTENT_TYPES", ["text/html", "text/*"], True),
        ("CONTENT_TYPES", ["Text/HTML"], False),
        ("CONTENT_TYPES", ["*/*"], False),
        ("CONTENT_TYPES", ["application/*+json", "image/*+xml"], True),
        ("CONTENT_TYPES", ["application/*+"], False),
        ("CONTENT_TYPES", ["application/x*+json"], False),
        ("CONTENT_TYPES", "text/html", False),
        ("STREAMS", "proxy", False),
        ("STREAMS", 1, False),
        ("SSE_TOPICS", "shared", True),
        ("PADDING", 0, True),
        ("COALESCE", float("inf"), False),
        ("COALESCE", -1, False),
        ("COALESCE", True, False),
        ("COALESCE", "2", False),
        ("COALESCE", 0.002, True),
        ("COALESCE", "2ms", True),
        ("RESPECT_NO_TRANSFORM", None, False),
        ("EXCLUDE", None, True),
        ("EXCLUDE", "", False),
    ],
)
def test_shapes(key: str, value: object, usable) -> None:
    shape = SHAPE.keys[key]
    assert isinstance(shape, Shape)
    assert shape.usable(value) is usable


def test_misfits_name_the_key_and_the_value() -> None:
    found = misfits({"MIN_SIZE": "big", "ORDER": {"gzip": 1}, "SSE": True}, SHAPE)
    assert found == [
        Misfit(("ORDER",), "a list of codec tokens", "a dict"),
        Misfit(("MIN_SIZE",), "an integer from 0", "'big'"),
    ]


class TestCompressionSettings:
    """`compression_settings` reads the merged mapping again once it moves."""

    def test_the_read_is_held_until_the_mapping_moves(self) -> None:
        first = compression_settings()
        assert compression_settings() is first
        with override_next_settings(COMPRESSION={"PADDING": 0}):
            moved = compression_settings()
            assert moved.padding == 0
        assert compression_settings().padding == 32
