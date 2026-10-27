import asyncio
import gc

import pytest

from next.realtime.pool import PooledSignal
from next.testing import capture_signals


class _Slotted:
    """A sender no weak reference reaches, as a handler object with bare slots."""

    __slots__ = ()


class _Weak:
    """A sender a weak reference reaches."""


@pytest.mark.parametrize("asynchronous", [False, True])
def test_a_sender_without_weak_references_reaches_its_receivers(
    *, asynchronous: bool
) -> None:
    signal = PooledSignal(use_caching=True)
    sender = _Slotted()
    with capture_signals(signal) as recorder:
        for _ in range(2):
            if asynchronous:
                asyncio.run(signal.asend_robust(sender=sender, n=1))
            else:
                signal.send_robust(sender=sender, n=1)
    assert [event.sender for event in recorder] == [sender, sender]
    assert len(signal.sender_receivers_cache) == 0


def test_a_weakly_referenced_sender_is_cached_until_it_goes() -> None:
    signal = PooledSignal(use_caching=True)
    sender = _Weak()
    cache = signal.sender_receivers_cache
    with capture_signals(signal):
        signal.send_robust(sender=sender)
        assert list(cache) == [sender]
        del cache[sender]
        assert len(cache) == 0
    cache[sender] = []
    del sender
    gc.collect()
    assert len(cache) == 0


def test_removing_a_sender_without_weak_references_is_a_miss() -> None:
    cache = PooledSignal(use_caching=True).sender_receivers_cache
    with pytest.raises(KeyError):
        del cache[_Slotted()]
    with pytest.raises(KeyError):
        cache[_Slotted()]


def test_an_uncached_signal_keeps_the_plain_cache() -> None:
    assert PooledSignal().sender_receivers_cache == {}
