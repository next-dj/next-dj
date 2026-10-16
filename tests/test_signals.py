import ast
import gc
import weakref
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models.signals import post_save
from django.db.utils import ConnectionDoesNotExist
from django.dispatch import Signal

from next import signals as framework_signals
from next.components import signals as component_signals
from next.consent import signals as consent_signals
from next.forms import signals as form_signals
from next.partial import signals as partial_signals
from next.seo import signals as seo_signals
from next.signals import receiver
from next.static import signals as static_signals
from next.static.assets import StaticAsset
from next.static.manager import default_manager
from next.static.signals import asset_registered
from next.testing import capture_signals
from next.urls import signals as url_signals


_SIGNAL_AREAS = (
    component_signals,
    consent_signals,
    form_signals,
    partial_signals,
    seo_signals,
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

EXPORTED_SIGNALS = frozenset(
    name
    for name in framework_signals.__all__
    if isinstance(getattr(framework_signals, name), Signal)
)

CACHED_SIGNALS = EXPORTED_SIGNALS - UNCACHED_SIGNALS

# One per settings-driven backend family, all sent from `next.backends`.
BACKEND_LOADED_SIGNALS = frozenset(
    {
        "component_backend_loaded",
        "consent_backend_loaded",
        "form_backend_loaded",
        "partial_backend_loaded",
        "router_backend_loaded",
        "sitemap_backend_loaded",
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

    Caching keys the receiver lookup per sender, so a render guard asks lock-free.
    """

    @pytest.mark.parametrize("name", sorted(CACHED_SIGNALS))
    def test_cached_signal_declares_use_caching(self, name: str) -> None:
        assert _signal(name).use_caching is True

    @pytest.mark.parametrize("name", sorted(UNCACHED_SIGNALS))
    def test_excluded_signal_stays_uncached(self, name: str) -> None:
        assert _signal(name).use_caching is False

    def test_every_exported_signal_is_classified(self) -> None:
        assert EXPORTED_SIGNALS == CACHED_SIGNALS | UNCACHED_SIGNALS


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


class _RollbackError(Exception):
    """Raised inside an atomic block to roll it back."""


def _send_and_roll_back(signal: Signal, sender: type, **kwargs: object) -> None:
    with transaction.atomic():
        signal.send(sender=sender, **kwargs)
        raise _RollbackError


@pytest.fixture()
def signal() -> Signal:
    return Signal()


def _recorder(calls: list[tuple[object, dict[str, object]]]):
    def handler(sender: object, **kwargs: object) -> str:
        calls.append((sender, kwargs))
        return "handled"

    return handler


class _MethodHandlers:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def handle(self, sender: object, **kwargs: object) -> None:
        self.calls.append(sender)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("on_commit", [False, True], ids=["plain", "on_commit"])
class TestReceiverEitherWay:
    """Both connections hand back the decorated function and honour a sender filter."""

    def test_the_decorated_function_comes_back(
        self, signal: Signal, *, on_commit: bool
    ) -> None:
        handler = _recorder([])
        assert receiver(signal, on_commit=on_commit)(handler) is handler

    def test_a_sender_filter_holds(self, signal: Signal, *, on_commit: bool) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        handler = _recorder(calls)
        receiver(signal, on_commit=on_commit, sender=_GuardedSender)(handler)
        signal.send(sender=_OtherSender)
        signal.send(sender=_GuardedSender)
        assert [sender for sender, _kwargs in calls] == [_GuardedSender]


class TestReceiverWithoutOnCommit:
    """Without `on_commit` the decorator behaves exactly like Django's `receiver`."""

    def test_the_handler_runs_inside_send_and_answers_it(self, signal: Signal) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        handler = receiver(signal)(_recorder(calls))
        assert signal.send(sender=_GuardedSender, value=1) == [(handler, "handled")]
        assert calls == [(_GuardedSender, {"signal": signal, "value": 1})]

    def test_the_connection_is_weak_by_default(self, signal: Signal) -> None:
        receiver(signal)(_recorder([]))
        gc.collect()
        assert signal.send(sender=_GuardedSender) == []

    def test_weak_false_keeps_the_handler_alive(self, signal: Signal) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        receiver(signal, weak=False)(_recorder(calls))
        gc.collect()
        signal.send(sender=_GuardedSender)
        assert len(calls) == 1


@pytest.mark.django_db(transaction=True)
class TestReceiverOnCommit:
    """With `on_commit` the handler waits for the commit of the sending transaction."""

    def test_autocommit_runs_the_handler_at_once(self, signal: Signal) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        receiver(signal, on_commit=True)(_recorder(calls))
        signal.send(sender=_GuardedSender, value=1)
        assert calls == [(_GuardedSender, {"signal": signal, "value": 1})]

    def test_the_handler_waits_for_the_commit(self, signal: Signal) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        receiver(signal, on_commit=True)(_recorder(calls))
        with transaction.atomic():
            signal.send(sender=_GuardedSender, value=1)
            assert calls == []
        assert calls == [(_GuardedSender, {"signal": signal, "value": 1})]

    def test_a_rollback_drops_the_handler(self, signal: Signal) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        receiver(signal, on_commit=True)(_recorder(calls))
        with pytest.raises(_RollbackError):
            _send_and_roll_back(signal, _GuardedSender)
        assert calls == []

    def test_a_rolled_back_savepoint_drops_only_its_own_send(
        self, signal: Signal
    ) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        receiver(signal, on_commit=True)(_recorder(calls))
        with transaction.atomic():
            signal.send(sender=_GuardedSender, kept=True)
            with pytest.raises(_RollbackError):
                _send_and_roll_back(signal, _OtherSender, kept=False)
        assert calls == [(_GuardedSender, {"signal": signal, "kept": True})]

    def test_the_return_value_never_reaches_send(self, signal: Signal) -> None:
        receiver(signal, on_commit=True)(_recorder([]))
        responses = [response for _r, response in signal.send(sender=_GuardedSender)]
        assert responses == [None]

    def test_the_connection_outlives_the_last_reference(self, signal: Signal) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        receiver(signal, on_commit=True)(_recorder(calls))
        gc.collect()
        signal.send(sender=_GuardedSender)
        assert len(calls) == 1

    def test_an_explicit_weak_false_is_accepted(self, signal: Signal) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        receiver(signal, on_commit=True, weak=False)(_recorder(calls))
        signal.send(sender=_GuardedSender)
        assert len(calls) == 1

    def test_a_dispatch_uid_connects_once(self, signal: Signal) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        handler = _recorder(calls)
        receiver(signal, on_commit=True, dispatch_uid="once")(handler)
        receiver(signal, on_commit=True, dispatch_uid="once")(handler)
        signal.send(sender=_GuardedSender)
        assert len(calls) == 1

    def test_one_function_decorated_twice_connects_once(self, signal: Signal) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        handler = _recorder(calls)
        receiver(signal, on_commit=True)(handler)
        receiver(signal, on_commit=True)(handler)
        signal.send(sender=_GuardedSender)
        assert len(calls) == 1

    def test_two_functions_both_connect(self, signal: Signal) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        receiver(signal, on_commit=True)(_recorder(calls))
        receiver(signal, on_commit=True)(_recorder(calls))
        signal.send(sender=_GuardedSender)
        assert len(calls) == 2

    def test_one_bound_method_decorated_twice_connects_once(
        self, signal: Signal
    ) -> None:
        handlers = _MethodHandlers()
        receiver(signal, on_commit=True)(handlers.handle)
        receiver(signal, on_commit=True)(handlers.handle)
        signal.send(sender=_GuardedSender)
        assert handlers.calls == [_GuardedSender]

    def test_one_function_connects_once_per_sender(self, signal: Signal) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        handler = _recorder(calls)
        receiver(signal, on_commit=True, sender=_GuardedSender)(handler)
        receiver(signal, on_commit=True, sender=_OtherSender)(handler)
        signal.send(sender=_GuardedSender)
        signal.send(sender=_OtherSender)
        assert [sender for sender, _kwargs in calls] == [_GuardedSender, _OtherSender]

    def test_disconnecting_the_function_removes_the_handler(
        self, signal: Signal
    ) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        handler = receiver(signal, on_commit=True)(_recorder(calls))
        assert signal.disconnect(handler) is True
        signal.send(sender=_GuardedSender)
        assert calls == []

    def test_disconnecting_the_bound_method_removes_the_handler(
        self, signal: Signal
    ) -> None:
        handlers = _MethodHandlers()
        receiver(signal, on_commit=True)(handlers.handle)
        assert signal.disconnect(handlers.handle) is True
        signal.send(sender=_GuardedSender)
        assert handlers.calls == []

    def test_a_plain_connection_of_the_same_function_wins(self, signal: Signal) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        handler = receiver(signal)(_recorder(calls))
        receiver(signal, on_commit=True)(handler)
        with transaction.atomic():
            signal.send(sender=_GuardedSender)
            assert len(calls) == 1
        assert len(calls) == 1

    def test_a_signal_list_connects_every_signal(self) -> None:
        first, second = Signal(), Signal()
        calls: list[tuple[object, dict[str, object]]] = []
        receiver([first, second], on_commit=True)(_recorder(calls))
        with transaction.atomic():
            first.send(sender=_GuardedSender)
            second.send(sender=_OtherSender)
            assert calls == []
        assert [kwargs["signal"] for _sender, kwargs in calls] == [first, second]

    def test_a_coroutine_handler_runs_after_the_commit(self, signal: Signal) -> None:
        calls: list[tuple[object, dict[str, object]]] = []

        async def handler(sender: object, **kwargs: object) -> None:
            calls.append((sender, kwargs))

        assert receiver(signal, on_commit=True)(handler) is handler
        with transaction.atomic():
            signal.send(sender=_GuardedSender, value=2)
            assert calls == []
        assert calls == [(_GuardedSender, {"signal": signal, "value": 2})]

    @pytest.mark.parametrize(
        ("kwargs", "alias"),
        [
            pytest.param({"using": "default"}, "default", id="named"),
            pytest.param({"using": object()}, None, id="not_an_alias"),
            pytest.param({}, None, id="absent"),
        ],
    )
    def test_the_using_kwarg_picks_the_database(
        self, signal: Signal, kwargs: dict[str, object], alias: str | None
    ) -> None:
        receiver(signal, on_commit=True)(_recorder([]))
        with patch.object(
            transaction, "on_commit", wraps=transaction.on_commit
        ) as on_commit:
            signal.send(sender=_GuardedSender, **kwargs)
        assert on_commit.call_args.kwargs == {"using": alias}

    def test_an_unknown_alias_reaches_the_connection_lookup(
        self, signal: Signal
    ) -> None:
        receiver(signal, on_commit=True)(_recorder([]))
        with pytest.raises(ConnectionDoesNotExist, match="replica"):
            signal.send(sender=_GuardedSender, using="replica")

    def test_a_model_signal_waits_for_its_database_commit(self) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        receiver(post_save, on_commit=True, sender=Group, dispatch_uid="group-saved")(
            _recorder(calls)
        )
        try:
            with transaction.atomic():
                group = Group.objects.create(name="editors")
                assert calls == []
        finally:
            post_save.disconnect(sender=Group, dispatch_uid="group-saved")
        assert len(calls) == 1
        sender, kwargs = calls[0]
        assert sender is Group
        assert kwargs["instance"] == group
        assert kwargs["created"] is True
        assert kwargs["using"] == "default"


class TestReceiverRefusesWeakOnCommit:
    """A deferred handler is held strongly, so asking for a weak one is an error."""

    def test_weak_true_with_on_commit_raises(self, signal: Signal) -> None:
        with pytest.raises(ValueError, match=r"cannot connect weakly\. Drop weak=True"):
            receiver(signal, on_commit=True, weak=True)

    def test_the_refusal_connects_nothing(self, signal: Signal) -> None:
        with pytest.raises(ValueError, match="weakly"):
            receiver(signal, on_commit=True, weak=True)
        assert signal.has_listeners() is False
