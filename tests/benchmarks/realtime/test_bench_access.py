from collections.abc import Callable

import pytest

from next.realtime.access import Access, AccessCheck, TopicAuthorizer
from tests.benchmarks.factories import SteppingClock, topic_names
from tests.support.realtime import allow_room_topic


class _CountingAuthorizer:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, user: object, topic: str) -> bool:
        self.calls += 1
        return allow_room_topic(user, topic)


def _check(
    count: int, clock: Callable[[], float], authorizer: TopicAuthorizer
) -> AccessCheck:
    return AccessCheck(
        topic_names(count),
        authorizer=authorizer,
        session_key=None,
        user=None,
        interval=1.0,
        clock=clock,
    )


class TestBenchAccessCheck:
    """Recheck topic grants for one live connection."""

    @pytest.mark.parametrize("count", [1, 64])
    @pytest.mark.benchmark(group="realtime.access")
    def test_inside_throttle_window(self, count: int, benchmark) -> None:
        """The cached verdict answers without calling the authorizer."""
        access = _check(count, lambda: 0.0, allow_room_topic)
        access.check()
        verdict = benchmark(access.check)
        assert verdict is Access.GRANTED

        authorizer = _CountingAuthorizer()
        probe = _check(count, lambda: 0.0, authorizer)
        probe.check()
        before = authorizer.calls
        assert probe.check() is Access.GRANTED
        assert before == count
        assert authorizer.calls == before

    @pytest.mark.parametrize("count", [1, 64])
    @pytest.mark.benchmark(group="realtime.access")
    def test_outside_throttle_window(self, count: int, benchmark) -> None:
        """Every call finds the interval elapsed and asks the authorizer again."""
        access = _check(count, SteppingClock(10.0), allow_room_topic)
        verdict = benchmark(access.check)
        assert verdict is Access.GRANTED

        authorizer = _CountingAuthorizer()
        probe = _check(count, SteppingClock(10.0), authorizer)
        probe.check()
        before = authorizer.calls
        assert probe.check() is Access.GRANTED
        assert authorizer.calls == before + count
