import pytest

from next.conf.compression import (
    SHAPES,
    Misfit,
    compression_settings,
    misfits,
    read_settings,
)
from next.conf.defaults import DEFAULTS
from next.testing import override_next_settings


class TestReadSettings:
    """`read_settings` answers every key, the default for an omitted or misfit one."""

    def test_the_defaults(self) -> None:
        current = read_settings({})
        assert current.enabled is True
        assert current.backends == (
            "next.compression.ZstdBackend",
            "next.compression.BrotliBackend",
            "next.compression.GzipBackend",
        )
        assert current.order == ("zstd", "br", "gzip")
        assert dict(current.levels) == {"zstd": 3, "br": 4, "gzip": 4}
        assert dict(current.windows) == {"zstd": 17, "br": 18}
        assert (current.min_size, current.padding) == (1024, 32)
        assert (current.streams, current.sse, current.sse_topics) == (
            "self",
            True,
            "off",
        )
        assert (current.coalesce_ms, current.offload_bytes) == (2.0, 16384)
        assert current.respect_no_transform is True
        assert current.exclude is None
        assert "text/*" in current.content_types

    def test_the_shapes_cover_every_default_key(self) -> None:
        assert set(SHAPES) == set(DEFAULTS["COMPRESSION"])

    def test_a_codec_mapping_lays_over_its_default(self) -> None:
        current = read_settings({"LEVELS": {"gzip": 6}, "WINDOWS": {"br": 20}})
        assert dict(current.levels) == {"zstd": 3, "br": 4, "gzip": 6}
        assert dict(current.windows) == {"zstd": 17, "br": 20}

    def test_written_values_are_read(self) -> None:
        current = read_settings(
            {
                "ENABLED": False,
                "ORDER": ["gzip"],
                "MIN_SIZE": 0,
                "STREAMS": "middleware",
                "SSE_TOPICS": "reset",
                "COALESCE_MS": 0.5,
                "EXCLUDE": "myapp.rules.exclude",
            }
        )
        assert current.enabled is False
        assert current.order == ("gzip",)
        assert current.min_size == 0
        assert current.streams == "middleware"
        assert current.sse_topics == "reset"
        assert current.coalesce_ms == 0.5
        assert current.exclude == "myapp.rules.exclude"

    def test_a_misfit_reads_as_its_default(self) -> None:
        current = read_settings(
            {"MIN_SIZE": -1, "ORDER": "gzip", "LEVELS": {"br": "x"}}
        )
        assert current.min_size == 1024
        assert current.order == ("zstd", "br", "gzip")
        assert dict(current.levels) == {"zstd": 3, "br": 4, "gzip": 4}


@pytest.mark.parametrize(
    ("key", "value", "usable"),
    [
        ("ENABLED", 1, False),
        ("BACKENDS", ["a.B", ""], False),
        ("BACKENDS", ("a.B",), True),
        ("ORDER", "gzip", False),
        ("LEVELS", {"gzip": True}, False),
        ("LEVELS", {"": 1}, False),
        ("LEVELS", [("gzip", 1)], False),
        ("WINDOWS", {"zstd": 20}, True),
        ("MIN_SIZE", 1.5, False),
        ("MIN_SIZE", True, False),
        ("CONTENT_TYPES", ["text/html", "text/*"], True),
        ("CONTENT_TYPES", ["Text/HTML"], False),
        ("CONTENT_TYPES", ["*/*"], False),
        ("CONTENT_TYPES", "text/html", False),
        ("STREAMS", "proxy", False),
        ("STREAMS", 1, False),
        ("SSE_TOPICS", "shared", True),
        ("PADDING", 0, True),
        ("COALESCE_MS", float("inf"), False),
        ("COALESCE_MS", -1, False),
        ("COALESCE_MS", True, False),
        ("COALESCE_MS", 2, True),
        ("OFFLOAD_BYTES", "16k", False),
        ("RESPECT_NO_TRANSFORM", None, False),
        ("EXCLUDE", None, True),
        ("EXCLUDE", "", False),
    ],
)
def test_shapes(key: str, value: object, usable) -> None:
    assert SHAPES[key][0](value) is usable


def test_misfits_name_the_key_and_the_value() -> None:
    found = misfits({"MIN_SIZE": "big", "ORDER": {"gzip": 1}, "SSE": True, "X": 1})
    assert found == [
        Misfit("ORDER", "a list of codec tokens", "a dict"),
        Misfit("MIN_SIZE", "an integer from 0", "'big'"),
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
