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


class Answers:
    """Stand in for the project callables a dotted setting names."""

    def __init__(self, module: str) -> None:
        """Name the test module the callables live in."""
        self.age: object = None
        self.keys: object = None
        self.asked = 0
        self.age_path = f"{module}.age_of"
        self.keys_path = f"{module}.ring"

    def age_of(self, request: object, claims: object) -> object:
        return self.age

    def ring(self) -> object:
        self.asked += 1
        return self.keys


@pytest.fixture()
def answers(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Answers:
    """Answer the dotted `MAX_AGE` and key callables with what a test sets."""
    held = Answers(request.module.__name__)
    monkeypatch.setattr(held.age_path, held.age_of, raising=False)
    monkeypatch.setattr(held.keys_path, held.ring, raising=False)
    return held
