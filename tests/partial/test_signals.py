from django.dispatch import Signal

import next.signals as aggregate
from next.partial import signals


SIX_SIGNALS = (
    "zone_registered",
    "zone_rendered",
    "patch_op_registered",
    "field_validated",
    "sse_stream_opened",
    "sse_stream_closed",
)


class TestPackageSignals:
    """The package ships exactly the six named protocol signals."""

    def test_all_six_are_signals(self) -> None:
        for name in SIX_SIGNALS:
            assert isinstance(getattr(signals, name), Signal)

    def test_all_exported(self) -> None:
        assert set(signals.__all__) == set(SIX_SIGNALS)


class TestAggregateReexport:
    """The aggregate signals module re-exports every package signal."""

    def test_reexported_by_identity(self) -> None:
        for name in SIX_SIGNALS:
            assert getattr(aggregate, name) is getattr(signals, name)

    def test_present_in_aggregate_all(self) -> None:
        for name in SIX_SIGNALS:
            assert name in aggregate.__all__
