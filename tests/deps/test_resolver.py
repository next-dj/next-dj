import inspect
from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest

from next.deps import (
    DependencyCycleError,
    DependencyResolver,
    Depends,
    RegisteredParameterProvider,
    resolver,
)
from next.deps.cache import _IN_PROGRESS, DependencyCache
from next.urls import HttpRequestProvider, UrlKwargsProvider
from tests.support import (
    _ctx,
    _minimal_resolver,
    _resolver_with_form,
)


_IN_PROGRESS_SENTINEL = _IN_PROGRESS


class TestDependencyResolver:
    """Tests for DependencyResolver.resolve_dependencies."""

    def test_resolve_dependencies_injects_request_only(self, mock_http_request) -> None:
        """Only request is injected when function has single request param."""

        def fn(request: HttpRequest) -> str:
            return getattr(request, "path", "")

        r = _minimal_resolver()
        request = mock_http_request()
        result = r.resolve_dependencies(fn, request=request)
        assert result == {"request": request}

    def test_resolve_dependencies_injects_request_and_id(
        self, mock_http_request
    ) -> None:
        """Request and url_kwargs (id) are injected."""

        def fn(request: HttpRequest, id: int) -> None:  # noqa: A002
            pass

        r = _minimal_resolver()
        request = mock_http_request()
        result = r.resolve_dependencies(fn, request=request, id=42)
        assert result == {"request": request, "id": 42}

    def test_resolve_dependencies_injects_form(self, mock_http_request) -> None:
        """Request and form are injected when both in context."""

        def fn(request: HttpRequest, form: MagicMock) -> None:
            pass

        r = _resolver_with_form()
        request = mock_http_request()
        form = MagicMock()
        result = r.resolve_dependencies(fn, request=request, form=form)
        assert result == {"request": request, "form": form}

    def test_resolve_dependencies_empty_signature_returns_empty(self) -> None:
        """Empty dict when function has no parameters."""

        def fn() -> None:
            pass

        r = _minimal_resolver()
        result = r.resolve_dependencies(fn)
        assert result == {}

    def test_resolve_dependencies_skips_self(self, mock_http_request) -> None:
        """'self' is not included in resolved dict for bound methods."""

        class C:
            def method(self, request: HttpRequest) -> None:
                pass

        r = _minimal_resolver()
        request = mock_http_request()
        result = r.resolve_dependencies(C.method, request=request)
        assert "self" not in result
        assert result == {"request": request}

    def test_resolve_dependencies_skips_cls(self, mock_http_request) -> None:
        """'cls' is not included in resolved dict for classmethods."""

        class C:
            @classmethod
            def get_initial(cls, request: HttpRequest, id: int) -> dict:  # noqa: A002
                return {}

        r = _minimal_resolver()
        request = mock_http_request()
        result = r.resolve_dependencies(C.get_initial, request=request, id=1)
        assert "cls" not in result
        assert result == {"request": request, "id": 1}

    def test_resolve_dependencies_unknown_param_without_default_gets_none(
        self,
    ) -> None:
        """Params with no provider and no context value get None."""

        def fn(unknown: str) -> None:
            pass

        r = _minimal_resolver()
        result = r.resolve_dependencies(fn)
        assert result == {"unknown": None}

    def test_resolve_dependencies_skips_var_positional_and_var_keyword(
        self,
        mock_http_request,
    ) -> None:
        """*args and **kwargs are not included in resolved dict."""

        def fn(request: HttpRequest, *args: object, **kwargs: object) -> None:
            pass

        r = _minimal_resolver()
        request = mock_http_request()
        result = r.resolve_dependencies(fn, request=request)
        assert result == {"request": request}
        assert "args" not in result
        assert "kwargs" not in result

    def test_resolve_dependencies_custom_providers(self) -> None:
        """Custom providers can supply values for arbitrary param names."""

        def fn(x: int) -> None:
            pass

        class CustomProvider(RegisteredParameterProvider):
            def can_handle(self, param: inspect.Parameter, context: object) -> bool:
                return param.name == "x"

            def resolve(self, param: inspect.Parameter, context: object) -> object:
                return 100

        r = DependencyResolver(CustomProvider())
        result = r.resolve_dependencies(fn)
        assert result == {"x": 100}

    def test_resolve_dependencies_invalid_signature_returns_empty(self) -> None:
        """Non-callable or invalid signature yields empty dict."""
        r = _minimal_resolver()
        result = r.resolve_dependencies("not a callable")
        assert result == {}


class TestDependencyResolverConstruction:
    """Tests for DependencyResolver(*providers) and lazy provider loading."""

    def test_resolver_with_providers_stores_same_order(self) -> None:
        """DependencyResolver(p1, p2) stores providers in same order."""
        p1 = HttpRequestProvider()
        p2 = UrlKwargsProvider()
        instance = DependencyResolver(p1, p2)
        assert len(instance._providers) == 2
        assert instance._providers[0] is p1
        assert instance._providers[1] is p2

    def test_resolver_empty_uses_lazy_registry(self) -> None:
        """DependencyResolver() with no args loads providers from registry on first resolve."""
        instance = DependencyResolver()

        def fn(unknown: str) -> None:
            pass

        result = instance.resolve_dependencies(fn)
        assert result == {"unknown": None}


class TestDependencyResolverAddProvider:
    """Tests for add_provider on DependencyResolver."""

    def test_add_provider_appends_and_resolves(self) -> None:
        """After add_provider, custom param is resolved by added provider."""
        r = _minimal_resolver()

        class CustomProvider(RegisteredParameterProvider):
            def can_handle(self, param: inspect.Parameter, context: object) -> bool:
                return param.name == "x"

            def resolve(self, param: inspect.Parameter, context: object) -> object:
                return 99

        r.add_provider(CustomProvider())

        def fn(x: int) -> None:
            pass

        result = r.resolve_dependencies(fn)
        assert result == {"x": 99}


class TestResolverRegister:
    """Tests for resolver.register decorator and method."""

    def test_register_decorator_adds_provider_class(self) -> None:
        """``@resolver.register`` on a class registers an instance. The next resolve uses it."""
        initial_count = len(resolver._providers)

        @resolver.register
        class InjectedProvider(RegisteredParameterProvider):
            def can_handle(self, param: inspect.Parameter, context: object) -> bool:
                return param.name == "injected"

            def resolve(self, param: inspect.Parameter, context: object) -> object:
                return "from_register"

        try:
            assert len(resolver._providers) == initial_count + 1

            def fn(injected: str) -> None:
                pass

            result = resolver.resolve_dependencies(fn)
            assert result == {"injected": "from_register"}
        finally:
            resolver._providers.pop()

    def test_register_instance_adds_provider(self) -> None:
        """resolver.register(MyProvider()) adds the instance."""
        r = _minimal_resolver()

        class MyProvider(RegisteredParameterProvider):
            def can_handle(self, param: inspect.Parameter, context: object) -> bool:
                return param.name == "x"

            def resolve(self, param: inspect.Parameter, context: object) -> object:
                return 42

        r.register(MyProvider())

        def fn(x: int) -> None:
            pass

        result = r.resolve_dependencies(fn)
        assert result == {"x": 42}


class TestResolverResolveDependencies:
    """Tests for resolver.resolve_dependencies (global resolver)."""

    def test_resolve_dependencies_returns_resolved_dict(
        self, mock_http_request
    ) -> None:
        """resolver.resolve_dependencies returns request when only request declared."""

        def fn(request: HttpRequest) -> None:
            pass

        request = mock_http_request()
        result = resolver.resolve_dependencies(fn, request=request)
        assert result == {"request": request}

    def test_resolve_dependencies_with_url_kwargs(self) -> None:
        """Resolver passes url_kwargs as keyword args."""

        def fn(pk: int) -> None:
            pass

        result = resolver.resolve_dependencies(fn, pk=5)
        assert result == {"pk": 5}

    def test_resolve_dependencies_with_form(self) -> None:
        """Resolver passes form in context."""

        def fn(form: MagicMock) -> None:
            pass

        form = MagicMock()
        result = resolver.resolve_dependencies(fn, form=form)
        assert result == {"form": form}


class TestResolveWithTemplateContext:
    """DependencyResolver.resolve_with_template_context."""

    def test_explicit_request_wins_over_template_context(
        self, mock_http_request
    ) -> None:
        """Explicit request= wins over template_context['request']."""
        req_real = mock_http_request()
        req_wrong = mock_http_request()

        def fn(request: HttpRequest) -> None:
            pass

        r = DependencyResolver()
        result = r.resolve_with_template_context(
            fn,
            request=req_real,
            template_context={"request": req_wrong},
            _cache={},
            _stack=[],
        )
        assert result["request"] is req_real

    def test_form_taken_from_template_context(self) -> None:
        """Form instance is taken from template_context['form']."""
        form = MagicMock()

        def fn(form: MagicMock) -> None:
            pass

        r = DependencyResolver()
        result = r.resolve_with_template_context(
            fn,
            request=None,
            template_context={"form": form},
            _cache={},
            _stack=[],
        )
        assert result["form"] is form

    def test_uses_dependency_cache_instance_when_passed(self) -> None:
        """When _cache is a DependencyCache, that instance is used (values land in backing)."""
        r = DependencyResolver()

        def provide() -> str:
            return "cached"

        r.register_dependency("d", provide)

        def fn(x: str = Depends("d")) -> None:
            pass

        backing: dict[str, object] = {}
        dc = DependencyCache(backing_dict=backing)
        try:
            r.resolve_with_template_context(
                fn, request=None, template_context={}, _cache=dc
            )
            assert backing["d"] == "cached"
        finally:
            r._dependency_callables.pop("d", None)

    def test_creates_new_cache_when_cache_arg_none(self) -> None:
        """When _cache is None, a fresh DependencyCache is created."""

        def fn(x: str) -> None:
            pass

        r = DependencyResolver()
        result = r.resolve_with_template_context(
            fn, request=None, template_context={"x": "hi"}, _cache=None
        )
        assert result == {"x": "hi"}


class TestDependencyCycleError:
    """Tests for circular dependency detection."""

    def test_self_cycle_raises(self) -> None:
        """When a dependency needs itself (a -> a), DependencyCycleError is raised."""

        def get_a(a: str = Depends("a")) -> str:
            return f"a-{a}"

        resolver.register_dependency("a", get_a)
        try:

            def top(a: str = Depends("a")) -> str:
                return a

            cache: dict = {}
            stack: list[str] = []
            with pytest.raises(DependencyCycleError) as exc_info:
                resolver.resolve_dependencies(
                    top, request=None, _cache=cache, _stack=stack
                )
            cycle = exc_info.value.cycle
            assert "a" in cycle
            assert "Circular dependency" in str(exc_info.value)
        finally:
            resolver._dependency_callables.pop("a", None)

    def test_resolve_callable_dependency_returns_none_when_name_not_registered(
        self,
    ) -> None:
        """_resolve_callable_dependency returns None when name not in registry."""
        ctx = _ctx()
        result = resolver._resolve_callable_dependency("nonexistent", ctx)
        assert result is None

    def test_resolve_callable_dependency_raises_when_cache_has_in_progress(
        self,
    ) -> None:
        """When cache has name with _IN_PROGRESS but name not in stack, cycle is detected."""
        resolver.register_dependency("a", lambda: None)
        try:
            cache = {"a": _IN_PROGRESS_SENTINEL}
            stack = []
            ctx = _ctx(cache=cache, stack=stack)
            with pytest.raises(DependencyCycleError) as exc_info:
                resolver._resolve_callable_dependency("a", ctx)
            assert exc_info.value.cycle == ["a"]
        finally:
            resolver._dependency_callables.pop("a", None)

    def test_cycle_a_depends_on_b_b_depends_on_a_raises(self) -> None:
        """When A needs B and B needs A, DependencyCycleError is raised."""

        def get_a(b: str = Depends("b")) -> str:
            return f"a-{b}"

        def get_b(a: str = Depends("a")) -> str:
            return f"b-{a}"

        resolver.register_dependency("a", get_a)
        resolver.register_dependency("b", get_b)
        try:

            def top(a: str = Depends("a")) -> str:
                return a

            cache: dict = {}
            stack: list = []
            with pytest.raises(DependencyCycleError) as exc_info:
                resolver.resolve_dependencies(
                    top, request=None, _cache=cache, _stack=stack
                )
            cycle = exc_info.value.cycle
            assert "a" in cycle
            assert "b" in cycle
            assert "Circular dependency" in str(exc_info.value)
        finally:
            resolver._dependency_callables.pop("a", None)
            resolver._dependency_callables.pop("b", None)
