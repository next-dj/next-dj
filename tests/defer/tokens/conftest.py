from collections.abc import Iterator

import pytest
from django.core.cache import caches

from next.defer.tokens.seal import crypto_library
from next.testing.tokens import FrozenClock, clocked
from tests.support.tokens import NOW


@pytest.fixture(autouse=True)
def _fresh_token_state() -> Iterator[None]:
    """Forget the library probe and the cached claims around each test."""
    crypto_library.cache_clear()
    caches["default"].clear()
    yield
    crypto_library.cache_clear()
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
        self.age_path = f"{module}.age_of"

    def age_of(self, request: object, claims: object) -> object:
        return self.age


@pytest.fixture()
def answers(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Answers:
    """Answer the dotted `MAX_AGE` callable with what a test sets."""
    held = Answers(request.module.__name__)
    monkeypatch.setattr(held.age_path, held.age_of, raising=False)
    return held
