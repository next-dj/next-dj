from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.deps import Depends
from next.deps.cache import DependencyCache
from tests.support.deps import themed_resolver


if TYPE_CHECKING:
    from next.deps.resolver import DependencyResolver


_PLAN_ROUNDS = 300
_PLAN_WARMUP_ROUNDS = 10


def _settings() -> dict[str, object]:
    """Leaf factory, with nothing for the resolver to inject."""
    return {"currency": "EUR", "locale": "en"}


def _pricing(theme: str = Depends("theme")) -> dict[str, object]:
    """Injected factory, resolved through the plan of its own signature."""
    return {"theme": theme, "rate": 2}


def _first(settings: object = Depends(_settings)) -> object:
    return settings


def _second(settings: object = Depends(_settings)) -> object:
    return settings


def _third(
    settings: object = Depends(_settings), pricing: object = Depends(_pricing)
) -> object:
    return settings, pricing


def _fourth(pricing: object = Depends(_pricing)) -> object:
    return pricing


_UNIT = (_first, _second, _third, _fourth, _first, _fourth)


def _render_unit(planned: DependencyResolver) -> list[dict[str, object]]:
    """Resolve the callables of one render unit against the cache they share."""
    cache = DependencyCache()
    return [planned.resolve_dependencies(func, _cache=cache) for func in _UNIT]


class TestBenchFactoryDepends:
    """`Depends(factory)` across the callables of one render unit, called once each."""

    @pytest.mark.benchmark(group="deps.markers")
    def test_unit_shares_its_factories(self, benchmark) -> None:
        """Six callables naming two factories, so four of their reads hit the cache."""
        planned = themed_resolver()
        results = benchmark(_render_unit, planned)
        assert results[0]["settings"] is results[1]["settings"]
        assert results[2]["pricing"] is results[3]["pricing"]

    @pytest.mark.benchmark(group="deps.markers")
    def test_unit_first_use_compiles_its_plans(self, benchmark) -> None:
        """The same unit on a fresh resolver, which compiles a plan per callable."""

        def setup() -> tuple[tuple[DependencyResolver], dict[str, object]]:
            return (themed_resolver(),), {}

        benchmark.pedantic(
            _render_unit,
            setup=setup,
            rounds=_PLAN_ROUNDS,
            warmup_rounds=_PLAN_WARMUP_ROUNDS,
        )

    @pytest.mark.benchmark(group="deps.markers")
    def test_leaf_factory_memo_hit(self, benchmark) -> None:
        """One consumer of a factory an earlier callable of the unit already ran."""
        planned = themed_resolver()
        cache = DependencyCache()
        first = planned.resolve_dependencies(_first, _cache=cache)

        def read() -> dict[str, object]:
            return planned.resolve_dependencies(_second, _cache=cache)

        assert benchmark(read)["settings"] is first["settings"]

    @pytest.mark.benchmark(group="deps.markers")
    def test_injected_factory_memo_miss(self, benchmark) -> None:
        """The first consumer of an injected factory, a fresh cache every call."""
        planned = themed_resolver()

        def read() -> dict[str, object]:
            return planned.resolve_dependencies(_fourth, _cache=DependencyCache())

        assert benchmark(read)["pricing"] == {"theme": "dark", "rate": 2}
