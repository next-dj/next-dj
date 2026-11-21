from collections.abc import Iterator

import pytest
from django.core.cache import caches

from next.suspense.tokens import FrozenClock, clocked
from next.suspense.tokens.codec import msgpack_library
from next.suspense.tokens.seal import forget_crypto
from tests.support.tokens import NOW


@pytest.fixture(autouse=True)
def _fresh_token_state() -> Iterator[None]:
    """Forget library probes, derived keys and cached claims around each test."""
    forget_crypto()
    msgpack_library.cache_clear()
    caches["default"].clear()
    yield
    forget_crypto()
    msgpack_library.cache_clear()
    caches["default"].clear()


@pytest.fixture()
def clock() -> Iterator[FrozenClock]:
    with clocked(FrozenClock(NOW)) as frozen:
        yield frozen
