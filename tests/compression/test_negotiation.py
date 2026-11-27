import pytest

from next.compression.negotiation import MAX_HEADER, negotiate, parse_accept_encoding


SERVER = ("zstd", "br", "gzip")


@pytest.mark.parametrize(
    ("header", "offered", "expected"),
    [
        ("gzip, deflate, br, zstd", SERVER, "zstd"),
        ("gzip, deflate, br", SERVER, "br"),
        ("gzip", SERVER, "gzip"),
        ("deflate", SERVER, None),
        ("", SERVER, None),
        ("identity", SERVER, None),
        ("gzip", (), None),
        ("zstd;q=0, br;q=0.5, gzip", SERVER, "gzip"),
        ("br;q=0.5, gzip;q=0.5", SERVER, "br"),
        ("gzip;q=1.0, br;q=0.999", SERVER, "gzip"),
        ("*", SERVER, "zstd"),
        ("*;q=0.5, zstd;q=0", SERVER, "br"),
        ("*;q=0", SERVER, None),
        ("identity;q=0", SERVER, None),
        ("GZIP ; Q=0.8", SERVER, "gzip"),
        ("  br  ,  gzip  ", ("gzip", "br"), "gzip"),
        ("x-gzip", SERVER, "gzip"),
        ("gzip;q=abc", SERVER, None),
        ("gzip;q=1.5", SERVER, None),
        ("gzip;q=0.1234", SERVER, None),
        ("gzip;q", SERVER, None),
        ("gzip;level=9", SERVER, "gzip"),
        ("gzip;q=0, gzip;q=1", SERVER, None),
        (",,; ;,", SERVER, None),
        ("gz ip, br", SERVER, "br"),
        ("br", ("gzip",), None),
    ],
)
def test_negotiate(header: str, offered: tuple[str, ...], expected) -> None:
    assert negotiate(header, offered) == expected


class TestParse:
    """`parse_accept_encoding` reads codings and weights and never raises."""

    def test_weights_in_header_order(self) -> None:
        assert parse_accept_encoding("gzip;q=0.5, br, *;q=0") == (
            ("gzip", 0.5),
            ("br", 1.0),
            ("*", 0.0),
        )

    @pytest.mark.parametrize("header", ["\x00\xff", ";;;", "q=1", "=", "a;b;c=d"])
    def test_malformed_input_never_raises(self, header: str) -> None:
        assert isinstance(parse_accept_encoding(header), tuple)

    def test_a_long_header_drops_the_element_the_cut_splits(self) -> None:
        header = "a" * (MAX_HEADER - 6) + ", br;q=0, gzip"
        assert parse_accept_encoding(header) == (("a" * (MAX_HEADER - 6), 1.0),)
        assert negotiate(header, ("br", "gzip")) is None

    def test_a_short_header_is_read_whole(self) -> None:
        assert len(parse_accept_encoding("gzip, br")) == 2
