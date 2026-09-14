import weakref

import pytest
from django.dispatch import Signal

from next import signals as framework_signals
from next.static.assets import StaticAsset
from next.static.manager import default_manager
from next.static.signals import asset_registered
from next.testing import capture_signals


UNCACHED_SIGNALS = frozenset(
    {
        "asset_registered",
        "collector_finalized",
        "provider_registered",
        "settings_reloaded",
    }
)

CACHED_SIGNALS = frozenset(framework_signals.__all__) - UNCACHED_SIGNALS


def _signal(name: str) -> Signal:
    return getattr(framework_signals, name)


def _noop_receiver(**kwargs: object) -> None:
    return None


class _GuardedSender:
    """The sender a guard asks about."""


class _OtherSender:
    """A sender some receiver is attached to instead of the guarded one."""


class TestFrameworkSignalCaching:
    """`use_caching` is on wherever every sender is a stable weak-referenceable object.

    A cached signal keys its receiver lookup on a `weakref.WeakKeyDictionary` entry for
    the sender, which buys a lock-free `has_listeners` on the render hot paths and costs
    a sender that cannot be weak-referenced or is rebuilt for every send.
    """

    @pytest.mark.parametrize("name", sorted(CACHED_SIGNALS))
    def test_cached_signal_declares_use_caching(self, name: str) -> None:
        assert _signal(name).use_caching is True

    @pytest.mark.parametrize("name", sorted(UNCACHED_SIGNALS))
    def test_excluded_signal_stays_uncached(self, name: str) -> None:
        assert _signal(name).use_caching is False

    def test_every_exported_signal_is_classified(self) -> None:
        assert frozenset(framework_signals.__all__) == CACHED_SIGNALS | UNCACHED_SIGNALS


class TestAssetRegisteredStaysUncached:
    """`asset_registered` sends the `StaticAsset` itself, which has no `__weakref__`.

    Flipping the signal to `use_caching=True` turns every discovery emission into a
    `TypeError`, so the exclusion is pinned by the send rather than by the flag alone.
    """

    def test_static_asset_cannot_be_weak_referenced(self) -> None:
        asset = StaticAsset(url="/static/next/x.css", kind="css")
        with pytest.raises(TypeError, match="weak reference"):
            weakref.ref(asset)

    def test_send_reaches_a_listener_with_the_asset_as_sender(self) -> None:
        asset = StaticAsset(url="/static/next/x.css", kind="css")
        collector = default_manager.create_collector()
        with capture_signals(asset_registered) as recorder:
            asset_registered.send(sender=asset, collector=collector, backend=None)
        assert recorder.first_for(asset_registered).sender is asset


class TestCollectorFinalizedStaysUncached:
    """`collector_finalized` sends a collector built for one render.

    The sender is weak-referenceable, so caching would not raise. It would only add a
    dictionary write per render behind an entry no later send can ever read.
    """

    def test_each_render_gets_its_own_collector(self) -> None:
        first = default_manager.create_collector()
        second = default_manager.create_collector()
        assert first is not second
        assert weakref.ref(second) is not None


class TestCachedSignalSenderContract:
    """Django reads the sender cache before it looks at the receiver list.

    Even the `None` default of a bare `has_listeners()` needs a weak-referenceable
    sender, so every core guard passes the sender its send uses.
    """

    def test_has_listeners_without_a_sender_raises_on_a_cached_signal(self) -> None:
        signal = Signal(use_caching=True)
        with pytest.raises(TypeError, match="weak reference"):
            signal.has_listeners()

    def test_has_listeners_answers_false_for_a_weak_referenceable_sender(self) -> None:
        signal = Signal(use_caching=True)
        assert signal.has_listeners(StaticAsset) is False

    def test_has_listeners_answers_true_once_a_receiver_connects(self) -> None:
        signal = Signal(use_caching=True)
        with capture_signals(signal):
            assert signal.has_listeners(StaticAsset) is True
        assert signal.has_listeners(StaticAsset) is False


class TestTwoStageListenerGuard:
    """The two-stage guard answers exactly what `has_listeners` alone answers.

    A non-empty receiver list is necessary for a signal to have a listener, so reading
    the attribute first settles a signal nobody connected to without building a sender
    cache entry. Django opens its own `Signal.send` with the same check.
    """

    def test_no_receiver_at_all_answers_false_through_both_forms(self) -> None:
        signal = Signal(use_caching=True)

        assert not signal.receivers
        assert signal.has_listeners(_GuardedSender) is False

    def test_a_receiver_for_another_sender_answers_false_through_both_forms(
        self,
    ) -> None:
        signal = Signal(use_caching=True)
        signal.connect(_noop_receiver, sender=_OtherSender, weak=False)

        assert signal.receivers
        assert signal.has_listeners(_GuardedSender) is False
        assert bool(signal.receivers and signal.has_listeners(_GuardedSender)) is False

    def test_a_receiver_for_this_sender_answers_true_through_both_forms(self) -> None:
        signal = Signal(use_caching=True)
        signal.connect(_noop_receiver, sender=_GuardedSender, weak=False)

        assert signal.has_listeners(_GuardedSender) is True
        assert bool(signal.receivers and signal.has_listeners(_GuardedSender)) is True
