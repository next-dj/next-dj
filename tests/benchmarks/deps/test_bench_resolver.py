from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from next.deps import Depends
from next.deps.resolver import DependencyResolver
from next.pages.context import Context


def _handler_simple(request: object, name: str = "default") -> str:
    del request
    return name


def _handler_five(
    request: object,
    a: int = 1,
    b: int = 2,
    c: int = 3,
    d: int = 4,
) -> int:
    del request
    return a + b + c + d


def _handler_mixed(
    request: object,
    cached: str = Depends("theme"),
    value: int = Context("page_value"),
) -> str:
    del request
    return f"{cached}:{value}"


class TestBenchDependencyResolver:
    @pytest.mark.benchmark(group="deps.resolver")
    def test_resolve_simple(self, benchmark) -> None:
        """Signature walk with one positional-only ``request`` + default kwarg."""
        resolver = DependencyResolver()
        request = MagicMock()
        benchmark(
            resolver.resolve_dependencies,
            _handler_simple,
            request=request,
        )

    @pytest.mark.benchmark(group="deps.resolver")
    def test_resolve_five_params(self, benchmark) -> None:
        """Five-parameter function — measures per-arg overhead."""
        resolver = DependencyResolver()
        request = MagicMock()
        benchmark(
            resolver.resolve_dependencies,
            _handler_five,
            request=request,
        )

    @pytest.mark.benchmark(group="deps.resolver")
    def test_resolve_mixed_markers(self, benchmark) -> None:
        """Mix of ``Depends`` and ``Context`` markers — provider chain cost."""
        resolver = DependencyResolver()
        resolver.dependency("theme")(lambda: "dark")
        request = MagicMock()
        benchmark(
            resolver.resolve_dependencies,
            _handler_mixed,
            request=request,
            _context_data={"page_value": 42},
        )
