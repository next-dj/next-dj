import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from next.realtime.limits import SocketLimits, TokenBucket, TunnelLimits, UpstreamLimits


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_the_bucket_spends_its_burst_then_refills_at_the_rate() -> None:
    clock = _Clock()
    bucket = TokenBucket(2.0, 3, clock=clock)
    assert [bucket.take() for _ in range(4)] == [True, True, True, False]
    clock.now = 0.5
    assert [bucket.take(), bucket.take()] == [True, False]
    clock.now = 100.0
    assert [bucket.take() for _ in range(4)] == [True, True, True, False]


def test_the_defaults_read_from_the_framework_settings() -> None:
    with override_settings(NEXT_FRAMEWORK={}):
        assert UpstreamLimits.configured() == UpstreamLimits(10.0, 20, 16384, 16, 10.0)
        assert TunnelLimits.configured() == TunnelLimits(4, 30.0, 1048576)


def test_partial_mappings_keep_the_other_defaults() -> None:
    realtime = {
        "UPSTREAM": {"RATE": 2, "CONCURRENCY": 3, "TIMEOUT": 1},
        "TUNNEL": {"TIMEOUT": 5},
    }
    with override_settings(NEXT_FRAMEWORK={"REALTIME": realtime}):
        assert UpstreamLimits.configured() == UpstreamLimits(2.0, 20, 16384, 3, 1.0)
        assert TunnelLimits.configured() == TunnelLimits(4, 5.0, 1048576)


def test_a_bucket_ignores_a_clock_that_steps_back() -> None:
    clock = _Clock()
    clock.now = 10.0
    bucket = TokenBucket(1.0, 1, clock=clock)
    assert bucket.take() is True
    clock.now = 5.0
    assert bucket.take() is False
    clock.now = 6.0
    assert bucket.take() is True


def test_socket_limits_read_both_settings() -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TUNNEL": False}}):
        limits = SocketLimits.configured()
    assert limits.tunnel is None
    assert limits.upstream.burst == 20


def test_a_false_tunnel_is_off() -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TUNNEL": False}}):
        assert TunnelLimits.configured() is None


@pytest.mark.parametrize(
    "upstream",
    [
        None,
        {"RATE": 0},
        {"BURST": 1.5},
        {"MAX_BYTES": 70000},
        {"CONCURRENCY": 65},
        {"TIMEOUT": 0},
        {"TIMEOUT": True},
        {"OTHER": 1},
    ],
)
def test_a_bad_upstream_raises(upstream: object) -> None:
    with (
        override_settings(NEXT_FRAMEWORK={"REALTIME": {"UPSTREAM": upstream}}),
        pytest.raises(ImproperlyConfigured),
    ):
        UpstreamLimits.configured()


@pytest.mark.parametrize(
    "tunnel", [True, None, {"CONCURRENCY": 0}, {"TIMEOUT": float("inf")}, {"OTHER": 1}]
)
def test_a_bad_tunnel_raises(tunnel: object) -> None:
    with (
        override_settings(NEXT_FRAMEWORK={"REALTIME": {"TUNNEL": tunnel}}),
        pytest.raises(ImproperlyConfigured),
    ):
        TunnelLimits.configured()
