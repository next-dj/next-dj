import inspect
from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest

from next.deps import (
    DependencyCycleError,
    DependencyResolver,
    Depends,
    RegisteredParameterProvider,
    ResolutionContext,
    resolver,
)
from next.deps.cache import _IN_PROGRESS, DependencyCache
from next.deps.resolver import (
    _introspect_key,
    _var_keyword_cache,
    cached_accepts_var_keyword,
)
from next.urls import HttpRequestProvider, UrlKwargsProvider
from tests.support import _ctx, _minimal_resolver, _resolver_with_form


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

        def fn(request: HttpRequest, obj_id: int) -> None:
            pass

        r = _minimal_resolver()
        request = mock_http_request()
        result = r.resolve_dependencies(fn, request=request, obj_id=42)
        assert result == {"request": request, "obj_id": 42}

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
            def get_initial(cls, request: HttpRequest, obj_id: int) -> dict:
                return {}

        r = _minimal_resolver()
        request = mock_http_request()
        result = r.resolve_dependencies(C.get_initial, request=request, obj_id=1)
        assert "cls" not in result
        assert result == {"request": request, "obj_id": 1}

    def test_resolve_dependencies_unknown_param_without_default_gets_none(self) -> None:
        """Params with no provider and no context value get None."""

        def fn(unknown: str) -> None:
            pass

        r = _minimal_resolver()
        result = r.resolve_dependencies(fn)
        assert result == {"unknown": None}

    def test_resolve_dependencies_skips_var_positional_and_var_keyword(
        self, mock_http_request
    ) -> None:
        """*args and **kwargs are not included in resolved dict."""

        def fn(request: HttpRequest, *args, **kwargs) -> None:
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

    def test_resolve_dependencies_reuses_passed_dependency_cache(self) -> None:
        """A pre-built `DependencyCache` passed as `_cache` is reused as-is."""
        r = DependencyResolver()
        cache = DependencyCache()

        @r.dependency("token")
        def token() -> str:
            return "abc"

        def fn(token: str = Depends("token")) -> str:
            return token

        result = r.resolve_dependencies(fn, _cache=cache)
        assert result == {"token": "abc"}
        assert "token" in cache

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


class TestDependencyResolverProviderOrder:
    """`prepend_provider` and `remove_provider` bracket a temporary provider."""

    def test_prepended_provider_wins_over_an_added_one(self) -> None:
        """The provider placed in front answers before the one appended earlier."""
        r = _minimal_resolver()

        class FirstProvider(RegisteredParameterProvider):
            def can_handle(self, param: inspect.Parameter, context: object) -> bool:
                return param.name == "x"

            def resolve(self, param: inspect.Parameter, context: object) -> object:
                return "appended"

        class SecondProvider(FirstProvider):
            def resolve(self, param: inspect.Parameter, context: object) -> object:
                return "prepended"

        r.add_provider(FirstProvider())
        winner = SecondProvider()
        r.prepend_provider(winner)

        def fn(x: str) -> None:
            pass

        assert r.resolve_dependencies(fn) == {"x": "prepended"}
        r.remove_provider(winner)
        assert r.resolve_dependencies(fn) == {"x": "appended"}

    def test_removing_a_provider_that_was_never_added_is_silent(self) -> None:
        """A double removal leaves the list alone instead of raising."""
        r = _minimal_resolver()

        class Stray(RegisteredParameterProvider):
            def can_handle(self, param: inspect.Parameter, context: object) -> bool:
                return False

            def resolve(self, param: inspect.Parameter, context: object) -> object:
                return None

        r.remove_provider(Stray())


class TestResolverDependencyBindings:
    """`get_dependency` and `unregister_dependency` read and drop a binding."""

    def test_get_returns_the_registered_callable(self) -> None:
        r = _minimal_resolver()

        def provide() -> str:
            return "value"

        r.register_dependency("thing", provide)
        assert r.get_dependency("thing") is provide

    def test_get_returns_none_for_an_unbound_name(self) -> None:
        assert _minimal_resolver().get_dependency("nothing") is None

    def test_unregister_drops_the_binding_and_tolerates_a_missing_one(self) -> None:
        r = _minimal_resolver()
        r.register_dependency("thing", lambda: "value")
        r.unregister_dependency("thing")
        r.unregister_dependency("thing")
        assert r.get_dependency("thing") is None


class TestResolverCurrentCallable:
    """`current_callable` is how a provider asks what the resolve is for."""

    def test_none_outside_a_resolve(self) -> None:
        assert _minimal_resolver().current_callable() is None

    def test_names_the_callable_being_resolved(self) -> None:
        r = _minimal_resolver()
        seen: list[object] = []

        class Recorder(RegisteredParameterProvider):
            def can_handle(self, param, context) -> bool:
                seen.append(r.current_callable())
                return False

            def resolve(self, param, context) -> object:
                raise NotImplementedError

        r.add_provider(Recorder())

        def handler(value: int = 1) -> int:
            return value

        r.resolve_dependencies(handler)
        assert seen == [handler]

    def test_a_nested_resolve_names_the_inner_callable(self) -> None:
        r = _minimal_resolver()
        seen: list[object] = []

        def provide_theme() -> str:
            return "dark"

        r.register_dependency("theme", provide_theme)

        class Recorder(RegisteredParameterProvider):
            priority = 10

            def can_handle(self, param, context) -> bool:
                seen.append(r.current_callable())
                return False

            def resolve(self, param, context) -> object:
                raise NotImplementedError

        r.prepend_provider(Recorder())

        def handler(theme: str = Depends("theme"), tail: int = 1) -> str:
            return f"{theme}{tail}"

        r.resolve_dependencies(handler)
        # The `Depends` resolve nests, and `provide_theme` takes no parameter,
        # so the outer handler is the only callable a `can_handle` sees.
        assert seen == [handler, handler]
        assert r.current_callable() is None


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
            fn, request=None, template_context={"form": form}, _cache={}, _stack=[]
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


class TestCachedAcceptsVarKeyword:
    """`cached_accepts_var_keyword` memoises the `**kwargs` answer per callable."""

    def test_true_for_var_keyword(self) -> None:
        def fn(**kwargs) -> None:
            return None

        assert cached_accepts_var_keyword(fn) is True

    def test_false_without_var_keyword(self) -> None:
        def fn(a: int, *args) -> int:
            return a

        assert cached_accepts_var_keyword(fn) is False

    def test_second_call_reuses_the_memo(self) -> None:
        def fn(**kwargs) -> None:
            return None

        key = _introspect_key(fn)
        try:
            assert cached_accepts_var_keyword(fn) is True
            # A poisoned memo entry proves the second call never re-inspects.
            _var_keyword_cache[key] = False
            assert cached_accepts_var_keyword(fn) is False
        finally:
            _var_keyword_cache.pop(key, None)

    def test_bound_method_keys_by_function(self) -> None:
        class Holder:
            def method(self, **kwargs) -> None:
                return None

        holder = Holder()
        key = _introspect_key(holder.method)
        try:
            assert cached_accepts_var_keyword(holder.method) is True
            assert key in _var_keyword_cache
        finally:
            _var_keyword_cache.pop(key, None)


class TestDependencyResolverProvides:
    """`provides` answers whether any registered provider fills a parameter."""

    def _context(self, request=None) -> ResolutionContext:
        return ResolutionContext(
            request=request,
            form=None,
            url_kwargs={},
            context_data={},
            cache=DependencyCache(),
        )

    def test_provides_true_for_a_claimed_parameter(self, mock_http_request) -> None:
        def fn(request: HttpRequest) -> None:
            return None

        instance = DependencyResolver()
        param = inspect.signature(fn).parameters["request"]
        assert instance.provides(fn, param, self._context(mock_http_request())) is True

    def test_provides_false_for_an_unclaimed_parameter(self) -> None:
        def fn(entries) -> None:
            return None

        instance = DependencyResolver()
        param = inspect.signature(fn).parameters["entries"]
        assert instance.provides(fn, param, self._context()) is False

    def test_provides_reads_the_annotation_through_the_callable(
        self, mock_http_request
    ) -> None:
        def fn(request: "HttpRequest") -> None:
            return None

        param = inspect.signature(fn).parameters["request"]
        assert param.annotation == "HttpRequest"
        assert resolver.provides(fn, param, self._context(mock_http_request())) is True

    def test_provides_leaves_the_resolve_stack_empty(self) -> None:
        def fn(entries) -> None:
            return None

        param = inspect.signature(fn).parameters["entries"]
        resolver.provides(fn, param, self._context())
        assert resolver.current_callable() is None
