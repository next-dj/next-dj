from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.support.tokens import (
    SEAL_PATHS,
    STORAGE_PATHS,
    claims,
    request_for,
    stacked,
    target,
)


if TYPE_CHECKING:
    from next.suspense.tokens import StackedTokenBackend


_FIRST_USE_ROUNDS = 200
_FIRST_USE_WARMUP_ROUNDS = 10


class TestBenchBoundaryToken:
    """Minting the token of a client-trip boundary and proving it on the reload."""

    @pytest.mark.benchmark(group="suspense.tokens")
    def test_issue(self, benchmark) -> None:
        """One signed inline token per client-trip wrapper a shell writes."""
        backend = stacked()
        request = request_for()
        held = claims(sku="a", n=3)
        token = benchmark(backend.issue, request, held)
        assert backend.redeem(request, token, target()).props == held.props

    @pytest.mark.benchmark(group="suspense.tokens")
    def test_open(self, benchmark) -> None:
        """The seal, the binding and the codec of a token, before a target is known."""
        backend = stacked()
        request = request_for()
        held = claims(sku="a", n=3)
        token = backend.issue(request, held)
        assert benchmark(backend.open, request, token).props == held.props

    @pytest.mark.benchmark(group="suspense.tokens")
    def test_redeem(self, benchmark) -> None:
        """A token opened and settled against the page and component it names."""
        backend = stacked()
        request = request_for()
        held = claims(sku="a", n=3)
        token = backend.issue(request, held)
        aimed = target()
        assert benchmark(backend.redeem, request, token, aimed).props == held.props

    @pytest.mark.benchmark(group="suspense.tokens")
    def test_issue_and_redeem_first_use(self, benchmark) -> None:
        """A fresh backend, as the first boundary after a settings reload pays it."""
        request = request_for()
        held = claims(sku="a", n=3)
        aimed = target()

        def round_trip(backend: StackedTokenBackend) -> object:
            return backend.redeem(request, backend.issue(request, held), aimed)

        def setup() -> tuple[tuple[StackedTokenBackend], dict[str, object]]:
            return (stacked(),), {}

        benchmark.pedantic(
            round_trip,
            setup=setup,
            rounds=_FIRST_USE_ROUNDS,
            warmup_rounds=_FIRST_USE_WARMUP_ROUNDS,
        )

    @pytest.mark.benchmark(group="suspense.tokens")
    def test_round_trip_through_cache_storage(self, benchmark) -> None:
        """Claims kept in the cache, so the token carries a reference alone."""
        backend = stacked(STORAGE=STORAGE_PATHS["cache"])
        request = request_for()
        held = claims(sku="a", n=3)
        aimed = target()

        def round_trip() -> object:
            return backend.redeem(request, backend.issue(request, held), aimed)

        assert round_trip().props == held.props
        benchmark(round_trip)

    @pytest.mark.benchmark(group="suspense.tokens")
    def test_round_trip_unsealed(self, benchmark) -> None:
        """The same round trip without a signature, which prices the seal itself."""
        backend = stacked(SEAL=SEAL_PATHS["none"])
        request = request_for()
        held = claims(sku="a", n=3)
        aimed = target()

        def round_trip() -> object:
            return backend.redeem(request, backend.issue(request, held), aimed)

        assert round_trip().props == held.props
        benchmark(round_trip)
