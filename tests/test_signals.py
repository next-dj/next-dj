import ast
import weakref
from pathlib import Path

import pytest
from django.dispatch import Signal

from next import signals as framework_signals
from next.components import signals as component_signals
from next.forms import signals as form_signals
from next.partial import signals as partial_signals
from next.static import signals as static_signals
from next.static.assets import StaticAsset
from next.static.manager import default_manager
from next.static.signals import asset_registered
from next.testing import capture_signals
from next.urls import signals as url_signals


_SIGNAL_AREAS = (
    component_signals,
    form_signals,
    partial_signals,
    static_signals,
    url_signals,
)


def _area_signals(name: str):
    """Return the area module that owns the signal the aggregate re-exports."""
    return next(area for area in _SIGNAL_AREAS if hasattr(area, name))


UNCACHED_SIGNALS = frozenset(
    {
        "asset_registered",
        "collector_finalized",
        "provider_registered",
        "settings_reloaded",
    }
)

CACHED_SIGNALS = frozenset(framework_signals.__all__) - UNCACHED_SIGNALS

# One per settings-driven backend family, all sent from `next.backends`.
BACKEND_LOADED_SIGNALS = frozenset(
    {
        "component_backend_loaded",
        "form_backend_loaded",
        "partial_backend_loaded",
        "router_backend_loaded",
        "static_backend_loaded",
        "wizard_backend_loaded",
    }
)


def _signal(name: str) -> Signal:
    return getattr(framework_signals, name)


def _noop_receiver(**kwargs: object) -> None:
    return None


def _reads_receivers(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    return any(
        isinstance(node, ast.Attribute) and node.attr == "receivers"
        for node in ast.walk(tree)
    )


class _GuardedSender:
    """The sender a guard asks about."""


class _OtherSender:
    """A sender some receiver is attached to instead of the guarded one."""


class TestFrameworkSignalCaching:
    """`use_caching` is on wherever every sender is a stable weak-referenceable object.

    Caching keys receiver lookup on a `WeakKeyDictionary` entry for the
    sender, trading a rebuilt-per-send sender for a lock-free `has_listeners`
    check on render hot paths.
    """

    @pytest.mark.parametrize("name", sorted(CACHED_SIGNALS))
    def test_cached_signal_declares_use_caching(self, name: str) -> None:
        assert _signal(name).use_caching is True

    @pytest.mark.parametrize("name", sorted(UNCACHED_SIGNALS))
    def test_excluded_signal_stays_uncached(self, name: str) -> None:
        assert _signal(name).use_caching is False

    def test_every_exported_signal_is_classified(self) -> None:
        assert frozenset(framework_signals.__all__) == CACHED_SIGNALS | UNCACHED_SIGNALS


class TestEveryBackendFamilyAnnouncesItsLoad:
    """One `*_backend_loaded` signal per settings-driven family, all aggregated here.

    A family added without one leaves a listener blind to half the loaded backends.
    """

    def test_the_aggregate_exports_one_signal_per_family(self) -> None:
        assert frozenset(framework_signals.__all__) >= BACKEND_LOADED_SIGNALS

    def test_the_aggregate_names_no_other_backend_signal(self) -> None:
        exported = {
            name
            for name in framework_signals.__all__
            if name.endswith("_backend_loaded")
        }
        assert exported == BACKEND_LOADED_SIGNALS

    @pytest.mark.parametrize("name", sorted(BACKEND_LOADED_SIGNALS))
    def test_the_aggregate_re_exports_the_signal_its_area_owns(self, name: str) -> None:
        assert _signal(name) is getattr(_area_signals(name), name)

    @pytest.mark.parametrize("name", sorted(BACKEND_LOADED_SIGNALS))
    def test_a_load_signal_carries_the_class_the_config_and_the_instance(
        self, name: str
    ) -> None:
        """Every family sends the same three, so one receiver serves them all."""
        signal = _signal(name)
        config = {"BACKEND": "myapp.Backend"}
        with capture_signals(signal) as recorder:
            signal.send(sender=StaticAsset, config=config, instance=None)

        event = recorder.first_for(signal)
        assert event.sender is StaticAsset
        assert event.kwargs == {"config": config, "instance": None}


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

    def test_a_collector_is_weak_referenceable(self) -> None:
        collector = default_manager.create_collector()
        assert weakref.ref(collector)() is collector


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


class TestNoReceiverListIntrospection:
    """No emitter reads `Signal.receivers`. The guard asks `has_listeners` instead."""

    def test_no_module_reads_the_receiver_list(self) -> None:
        package = Path(framework_signals.__file__).parent
        offenders = sorted(
            str(path.relative_to(package))
            for path in package.rglob("*.py")
            if _reads_receivers(path)
        )
        assert offenders == []


class TestGuardedEmittersAskPerSender:
    """The guards that remain answer one sender, never the whole receiver list."""

    def test_a_receiver_for_another_sender_leaves_the_guard_shut(self) -> None:
        signal = Signal(use_caching=True)
        signal.connect(_noop_receiver, sender=_OtherSender, weak=False)

        assert signal.has_listeners(_GuardedSender) is False

    def test_a_receiver_for_this_sender_opens_the_guard(self) -> None:
        signal = Signal(use_caching=True)
        signal.connect(_noop_receiver, sender=_GuardedSender, weak=False)

        assert signal.has_listeners(_GuardedSender) is True
