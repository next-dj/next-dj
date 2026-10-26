import asyncio

import pytest

from next.partial import Patches
from next.realtime.delivery import atransformed
from next.realtime.messages import Message
from tests.benchmarks.factories import topic_message


_MESSAGE = topic_message()


class _CountingTransform:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, message: Message) -> Patches:
        self.calls += 1
        return Patches(None).event("seen", {"seq": message.seq})


class _AsyncCountingTransform:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, message: Message) -> Patches:
        self.calls += 1
        return Patches(None).event("seen", {"seq": message.seq})


class TestBenchTransform:
    """Apply one subscriber's topic transform to a delivered message."""

    @pytest.mark.parametrize(
        "transform_class",
        [_AsyncCountingTransform, _CountingTransform],
        ids=["async", "sync"],
    )
    @pytest.mark.benchmark(group="realtime.transform")
    def test_atransformed(
        self, transform_class, bench_loop: asyncio.AbstractEventLoop, benchmark
    ) -> None:
        """An async transform is awaited on the loop, a sync one hops to the pool."""
        transform = transform_class()

        def run() -> Message | None:
            return bench_loop.run_until_complete(atransformed(_MESSAGE, transform))

        message = benchmark(run)
        assert message is not None
        assert message.seq == _MESSAGE.seq
        assert message.envelope.ops[0].extras == {
            "name": "seen",
            "detail": {"seq": _MESSAGE.seq},
        }
        before = transform.calls
        run()
        assert transform.calls == before + 1
