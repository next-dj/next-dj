from next import signals as framework_signals
from next.boundaries import boundary_failed


def test_the_aggregate_re_exports_the_signal() -> None:
    assert framework_signals.boundary_failed is boundary_failed
