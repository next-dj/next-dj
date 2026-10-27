import pytest

from next.realtime.transports import active_transports


class TestBenchTransports:
    """Resolve the transports the deployment offers."""

    @pytest.mark.benchmark(group="realtime.transports")
    def test_warm(self, benchmark) -> None:
        """The cached answer is a dictionary lookup."""
        active_transports()
        benchmark(active_transports)

    @pytest.mark.benchmark(group="realtime.transports")
    def test_cold(self, benchmark) -> None:
        """Each round re-reads the settings and installed apps after the cache drops."""
        benchmark.pedantic(
            active_transports,
            setup=active_transports.cache_clear,
            rounds=200,
            warmup_rounds=5,
        )
