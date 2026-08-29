from collections.abc import Generator

import pytest

from next.deps import RegisteredParameterProvider
from next.deps.signals import provider_registered
from next.testing import SignalRecorder, capture_signals


@pytest.fixture()
def capture_provider_registered() -> Generator[SignalRecorder, None, None]:
    """Record ``provider_registered`` emissions."""
    with capture_signals(provider_registered) as recorder:
        yield recorder


class TestProviderRegisteredSignal:
    """Tests for the provider_registered signal."""

    def test_signal_fires_when_subclass_defined(
        self, capture_provider_registered: SignalRecorder
    ) -> None:
        """provider_registered fires once when a new RegisteredParameterProvider subclass is defined."""

        class _TestProvider(RegisteredParameterProvider):
            def can_handle(self, param: object, context: object) -> bool:
                return False

            def resolve(self, param: object, context: object) -> object:
                return None

        assert len(capture_provider_registered) == 1
        assert capture_provider_registered.events[0].sender is _TestProvider

    def test_sender_is_the_subclass(
        self, capture_provider_registered: SignalRecorder
    ) -> None:
        """provider_registered sender is the newly defined subclass itself."""

        class _SenderCheckProvider(RegisteredParameterProvider):
            def can_handle(self, param: object, context: object) -> bool:
                return False

            def resolve(self, param: object, context: object) -> object:
                return None

        assert capture_provider_registered.events[0].sender is _SenderCheckProvider

    def test_signal_fires_for_each_subclass(
        self, capture_provider_registered: SignalRecorder
    ) -> None:
        """provider_registered fires once per subclass definition."""

        class _ProviderA(RegisteredParameterProvider):
            def can_handle(self, param: object, context: object) -> bool:
                return False

            def resolve(self, param: object, context: object) -> object:
                return None

        class _ProviderB(RegisteredParameterProvider):
            def can_handle(self, param: object, context: object) -> bool:
                return False

            def resolve(self, param: object, context: object) -> object:
                return None

        assert len(capture_provider_registered) == 2
        senders = {e.sender for e in capture_provider_registered}
        assert _ProviderA in senders
        assert _ProviderB in senders
