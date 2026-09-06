import pytest
from django.http import HttpRequest
from django.test import RequestFactory

from next.deps import Depends
from next.deps.resolver import (
    DependencyResolver,
    cached_accepts_var_keyword,
    cached_signature,
    cached_type_hints,
)
from next.pages.context import Context
from next.urls import DUrl
from tests.support import build_mock_http_request


def _handler_simple(request: object, name: str = "default") -> str:
    del request
    return name


def _handler_five(
    request: object, a: int = 1, b: int = 2, c: int = 3, d: int = 4
) -> int:
    del request
    return a + b + c + d


def _handler_simple_claimed(request: HttpRequest, name: str = "default") -> str:
    del request
    return name


def _handler_five_claimed(
    request: HttpRequest, a: int = 1, b: int = 2, c: int = 3, d: int = 4
) -> int:
    del request
    return a + b + c + d


def _handler_mixed(
    request: object, cached: str = Depends("theme"), value: int = Context("page_value")
) -> str:
    del request
    return f"{cached}:{value}"


def _handler_four_markers(
    request: HttpRequest,
    theme: str = Depends("theme"),
    value: int = Context("page_value"),
    slug: DUrl[str] = "",
) -> str:
    del request
    return f"{theme}:{value}:{slug}"


def _handler_component(value: int = Context("page_value"), key_3: str = "") -> str:
    return f"{value}:{key_3}"


_FOUR_MARKERS_KWARGS: dict[str, object] = {
    "request": RequestFactory().get("/"),
    "slug": "hello",
    "_context_data": {"page_value": 42},
}

_PLAN_ROUNDS = 300
_PLAN_WARMUP_ROUNDS = 10


def _template_context(size: int) -> dict[str, object]:
    """Context of `size` keys with the one `_handler_component` reads by marker."""
    return {"page_value": 42, **{f"key_{i}": i for i in range(1, size)}}


def _default_resolver() -> DependencyResolver:
    """Fresh resolver that loads the auto-registry on its first resolve."""
    return DependencyResolver()


def _themed_resolver() -> DependencyResolver:
    """Fresh resolver with its providers loaded and the `theme` dependency bound.

    The registry sync is paid here, so a plan benchmark measures the compile of
    one callable rather than the one-off instantiation of every provider.
    """
    planned = DependencyResolver()
    planned.dependency("theme")(lambda: "dark")
    planned._sync_providers()
    return planned


def _resolve_four_markers(planned: DependencyResolver) -> dict[str, object]:
    return planned.resolve_dependencies(_handler_four_markers, **_FOUR_MARKERS_KWARGS)


class TestBenchDependencyResolver:
    """Resolve cost per signature shape.

    The paired CI comparison matches runs by test id, so an id that already
    carries a baseline keeps measuring the shape that baseline measured and a
    new shape takes a new id rather than reusing one.
    """

    @pytest.mark.benchmark(group="deps.resolver")
    def test_direct_call_baseline(self, benchmark) -> None:
        """Plain call of the two-parameter handler, the floor every resolve adds to."""
        request = build_mock_http_request()
        benchmark(_handler_simple, request=request, name="default")

    @pytest.mark.benchmark(group="deps.resolver")
    def test_resolve_simple(self, benchmark) -> None:
        """Two parameters no provider claims, so both walk every candidate."""
        resolver = _default_resolver()
        request = build_mock_http_request()
        benchmark(resolver.resolve_dependencies, _handler_simple, request=request)

    @pytest.mark.benchmark(group="deps.resolver")
    def test_resolve_five_params(self, benchmark) -> None:
        """Five parameters no provider claims, the per-arg cost of a full walk."""
        resolver = _default_resolver()
        request = build_mock_http_request()
        benchmark(resolver.resolve_dependencies, _handler_five, request=request)

    @pytest.mark.benchmark(group="deps.resolver")
    def test_resolve_claimed_request_and_default(self, benchmark) -> None:
        """Plan replay over an ``HttpRequest`` annotation and one default kwarg.

        The compile narrows the request parameter to the one provider that can
        claim it, which still asks ``can_handle`` because only the context says
        whether a request is in flight.
        """
        resolver = _default_resolver()
        request = build_mock_http_request()
        benchmark(
            resolver.resolve_dependencies, _handler_simple_claimed, request=request
        )

    @pytest.mark.benchmark(group="deps.resolver")
    def test_resolve_five_claimed_params(self, benchmark) -> None:
        """Five parameters behind an ``HttpRequest`` annotation, the per-arg cost."""
        resolver = _default_resolver()
        request = build_mock_http_request()
        benchmark(resolver.resolve_dependencies, _handler_five_claimed, request=request)

    @pytest.mark.benchmark(group="deps.resolver")
    def test_resolve_mixed_markers(self, benchmark) -> None:
        """Mix of ``Depends`` and ``Context`` markers, provider chain cost."""
        resolver = _default_resolver()
        resolver.dependency("theme")(lambda: "dark")
        request = build_mock_http_request()
        benchmark(
            resolver.resolve_dependencies,
            _handler_mixed,
            request=request,
            _context_data={"page_value": 42},
        )

    @pytest.mark.benchmark(group="deps.resolver")
    def test_resolve_four_markers(self, benchmark) -> None:
        """``Depends`` + ``Context`` + ``DUrl`` + ``HttpRequest`` annotation."""
        resolver = _default_resolver()
        resolver.dependency("theme")(lambda: "dark")
        benchmark(
            resolver.resolve_dependencies, _handler_four_markers, **_FOUR_MARKERS_KWARGS
        )

    @pytest.mark.benchmark(group="deps.resolver")
    def test_resolve_with_template_context_20_keys(self, benchmark) -> None:
        """Component path, ``Context`` marker plus a name match over 20 keys."""
        resolver = _default_resolver()
        template_context = _template_context(20)
        benchmark(
            resolver.resolve_with_template_context,
            _handler_component,
            template_context=template_context,
        )

    @pytest.mark.benchmark(group="deps.resolver")
    def test_resolve_with_template_context_50_keys(self, benchmark) -> None:
        """Component path, ``Context`` marker plus a name match over 50 keys."""
        resolver = _default_resolver()
        template_context = _template_context(50)
        benchmark(
            resolver.resolve_with_template_context,
            _handler_component,
            template_context=template_context,
        )


class TestBenchInjectionPlan:
    """Cold compile against warm replay of the same four-marker handler.

    Both run under the same pedantic harness with a fresh resolver per round, so
    the gap between the two is the price of ``compile_plan`` for one callable.
    The signature and type-hint memos are process-wide and warm after the first
    round, which leaves the static-verdict walk as the cold cost.
    """

    @pytest.mark.benchmark(group="deps.plan")
    def test_compile_cold(self, benchmark) -> None:
        """First resolve of the handler on a resolver that has no plan for it."""

        def setup() -> tuple[tuple[DependencyResolver], dict[str, object]]:
            return (_themed_resolver(),), {}

        benchmark.pedantic(
            _resolve_four_markers,
            setup=setup,
            rounds=_PLAN_ROUNDS,
            warmup_rounds=_PLAN_WARMUP_ROUNDS,
        )

    @pytest.mark.benchmark(group="deps.plan")
    def test_replay_warm(self, benchmark) -> None:
        """Second resolve of the handler, the plan already sits in the cache."""

        def setup() -> tuple[tuple[DependencyResolver], dict[str, object]]:
            planned = _themed_resolver()
            _resolve_four_markers(planned)
            return (planned,), {}

        benchmark.pedantic(
            _resolve_four_markers,
            setup=setup,
            rounds=_PLAN_ROUNDS,
            warmup_rounds=_PLAN_WARMUP_ROUNDS,
        )


class TestBenchIntrospectionCache:
    """Warm reads of the per-callable introspection memos.

    Every page, component, and action callable goes through these on each
    resolve, and the form dispatcher reads the var-keyword one on its own.
    """

    @pytest.mark.benchmark(group="deps.introspection")
    def test_signature_hit(self, benchmark) -> None:
        cached_signature(_handler_five)
        benchmark(cached_signature, _handler_five)

    @pytest.mark.benchmark(group="deps.introspection")
    def test_type_hints_hit(self, benchmark) -> None:
        cached_type_hints(_handler_five)
        benchmark(cached_type_hints, _handler_five)

    @pytest.mark.benchmark(group="deps.introspection")
    def test_accepts_var_keyword_hit(self, benchmark) -> None:
        cached_accepts_var_keyword(_handler_five)
        benchmark(cached_accepts_var_keyword, _handler_five)
