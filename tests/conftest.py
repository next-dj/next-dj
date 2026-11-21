from collections.abc import Iterator

import pytest

from next.suspense import background_loop
from tests.django_setup import setup as _configure_django_for_tests


_configure_django_for_tests()


pytest_plugins = ["tests.fixtures"]


@pytest.fixture(autouse=True)
def _stopped_background_loop() -> Iterator[None]:
    """Join the process loop a test started, so the next test starts without it."""
    before = background_loop.current()
    yield
    if background_loop.current() is not before:
        background_loop.stop()
