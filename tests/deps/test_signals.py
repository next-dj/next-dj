from collections.abc import Generator
from typing import Any

import pytest

from next.deps import RegisteredParameterProvider
from next.deps.signals import provider_registered


@pytest.fixture()
def capture_provider_registered() -> Generator[list[dict[str, Any]], None, None]:
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    provider_registered.connect(_listener)
    try:
        yield events
    finally:
        provider_registered.disconnect(_listener)


class TestProviderRegisteredSignal:
    """Tests for the provider_registered signal."""

    def test_signal_fires_when_subclass_defined(
        self, capture_provider_registered: list[dict[str, Any]]
    ) -> None:
        """provider_registered fires once when a new RegisteredParameterProvider subclass is defined."""

        class _TestProvider(RegisteredParameterProvider):
            def can_handle(self, param: object, context: object) -> bool:
                return False

            def resolve(self, param: object, context: object) -> object:
                return None

        assert len(capture_provider_registered) == 1

    def test_sender_is_the_subclass(
        self, capture_provider_registered: list[dict[str, Any]]
    ) -> None:
        """provider_registered sender is the newly defined subclass itself."""

        class _SenderCheckProvider(RegisteredParameterProvider):
            def can_handle(self, param: object, context: object) -> bool:
                return False

            def resolve(self, param: object, context: object) -> object:
                return None

        assert capture_provider_registered[0]["sender"] is _SenderCheckProvider

    def test_signal_fires_for_each_subclass(
        self, capture_provider_registered: list[dict[str, Any]]
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
        senders = {e["sender"] for e in capture_provider_registered}
        assert _ProviderA in senders
        assert _ProviderB in senders
