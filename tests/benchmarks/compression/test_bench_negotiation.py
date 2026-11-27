import pytest

from next.compression import negotiation
from next.compression.negotiation import negotiate


_OFFERED = ("zstd", "br", "gzip")
_HEADERS = pytest.mark.parametrize(
    ("header", "coding"),
    [
        pytest.param("gzip, deflate, br, zstd", "zstd", id="browser"),
        pytest.param("gzip;q=1.0, identity; q=0.5, *;q=0", "gzip", id="weighted"),
    ],
)
_COLD_ROUNDS = 500
_COLD_WARMUP_ROUNDS = 20


class TestBenchNegotiate:
    """The coding one `Accept-Encoding` value picks among the offered ones."""

    @_HEADERS
    @pytest.mark.benchmark(group="compression.negotiate")
    def test_warm(self, header: str, coding: str, benchmark) -> None:
        """Every request after the first with the same header, answered from memo."""
        negotiate(header, _OFFERED)
        assert benchmark(negotiate, header, _OFFERED) == coding

    @_HEADERS
    @pytest.mark.benchmark(group="compression.negotiate")
    def test_cold(self, header: str, coding: str, benchmark) -> None:
        """The first request with a header, parsed and weighed from scratch."""

        def setup() -> tuple[tuple[str, tuple[str, ...]], dict[str, object]]:
            negotiation._choose.cache_clear()
            return (header, _OFFERED), {}

        chosen = benchmark.pedantic(
            negotiate,
            setup=setup,
            rounds=_COLD_ROUNDS,
            warmup_rounds=_COLD_WARMUP_ROUNDS,
        )
        assert chosen == coding
