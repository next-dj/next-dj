from next import signals as framework_signals
from next.permissions import signals as permission_signals


class TestAggregate:
    """The framework signal module re-exports the signal the area owns."""

    def test_permission_registered_is_re_exported(self) -> None:
        assert "permission_registered" in framework_signals.__all__
        assert (
            framework_signals.permission_registered
            is permission_signals.permission_registered
        )

    def test_the_area_exports_only_its_signal(self) -> None:
        assert permission_signals.__all__ == ["permission_registered"]
