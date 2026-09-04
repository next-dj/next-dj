import importlib
import inspect
import logging
import threading
from dataclasses import dataclass

import pytest
from django.http import HttpRequest

from next.deps import (
    DependencyCycleError,
    DependencyResolver,
    Depends,
    RegisteredParameterProvider,
    ResolutionContext,
    UnknownDependencyError,
    provider_registry,
    resolver,
)
from next.deps.cache import _IN_PROGRESS, DependencyCache
from next.deps.resolver import (
    _introspect_key,
    _signature_cache,
    _type_hints_cache,
    _var_keyword_cache,
    cached_accepts_var_keyword,
    cached_signature,
    cached_type_hints,
    forget_dep_caches,
)
from next.testing import make_resolution_context
from next.urls import HttpRequestProvider, UrlKwargsProvider
from tests.support import (
    AForm,
    DeferringProvider,
    _ctx,
    _minimal_resolver,
    _resolver_with_form,
    inspect_parameter,
)


_IN_PROGRESS_SENTINEL = _IN_PROGRESS

# The package attribute `next.deps.resolver` is the singleton, not the module.
_resolver_module = importlib.import_module("next.deps.resolver")


def _takes_probe_key(probe_key: str = "unset") -> str:
    return probe_key


_RESERVED_TEMPLATE_CONTEXT: dict[str, object] = {
    "request": "ctx-request",
    "form": "ctx-form",
    "cleaned_data": {"a": 1},
    "_cache": {},
    "_stack": [],
    "_context_data": {"probe_key": "nested"},
    "probe_key": "hi",
}


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

        def fn(request: HttpRequest, form: AForm) -> None:
            pass

        r = _resolver_with_form()
        request = mock_http_request()
        form = AForm()
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


class TestResolverErrorOwner:
    """A missing dependency is attributed to the callable whose plan raised."""

    def test_names_the_callable_being_resolved(self) -> None:
        def handler(theme: str = Depends("theme")) -> str:
            return theme

        with pytest.raises(UnknownDependencyError) as exc_info:
            DependencyResolver().resolve_dependencies(handler)
        assert exc_info.value.func is handler

    def test_a_nested_resolve_names_the_inner_callable(self) -> None:
        r = DependencyResolver()

        def provide_theme(missing: str = Depends("missing")) -> str:
            return missing

        def handler(theme: str = Depends("theme")) -> str:
            return theme

        r.register_dependency("theme", provide_theme)
        with pytest.raises(UnknownDependencyError) as exc_info:
            r.resolve_dependencies(handler)
        assert exc_info.value.func is provide_theme
        assert "provide_theme" in str(exc_info.value)


class TestResolverRegister:
    """Tests for resolver.register decorator and method."""

    def test_register_decorator_adds_provider_class(self) -> None:
        """``@register`` on a class registers an instance. The next resolve uses it."""
        r = _minimal_resolver()
        initial_count = len(r._providers)

        @r.register
        class InjectedProvider:
            def can_handle(self, param: inspect.Parameter, context: object) -> bool:
                return param.name == "injected"

            def resolve(self, param: inspect.Parameter, context: object) -> object:
                return "from_register"

            def static_can_handle(self, param: inspect.Parameter) -> bool | None:
                return None

        assert len(r._providers) == initial_count + 1
        assert isinstance(r._providers[-1], InjectedProvider)

        def fn(injected: str) -> None:
            pass

        assert r.resolve_dependencies(fn) == {"injected": "from_register"}

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

        def fn(form: AForm) -> None:
            pass

        form = AForm()
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
        form = AForm()

        def fn(form: AForm) -> None:
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
        r.resolve_with_template_context(
            fn, request=None, template_context={}, _cache=dc
        )
        assert backing["d"] == "cached"

    def test_creates_new_cache_when_cache_arg_none(self) -> None:
        """When _cache is None, a fresh DependencyCache is created."""

        def fn(x: str) -> None:
            pass

        r = DependencyResolver()
        result = r.resolve_with_template_context(
            fn, request=None, template_context={"x": "hi"}, _cache=None
        )
        assert result == {"x": "hi"}

    def test_uses_dict_cache_as_backing_store(self) -> None:
        r = DependencyResolver()
        r.register_dependency("d", lambda: "cached")

        def fn(x: str = Depends("d")) -> None:
            pass

        backing: dict[str, object] = {}
        assert r.resolve_with_template_context(fn, _cache=backing) == {"x": "cached"}
        assert backing == {"d": "cached"}

    @pytest.mark.parametrize(
        ("template_context", "expected_form", "expected_value"),
        [
            (None, None, "unset"),
            ({}, None, "unset"),
            ({"probe_key": "hi"}, None, "hi"),
            ({"form": "f", "probe_key": 1}, "f", 1),
            (dict(_RESERVED_TEMPLATE_CONTEXT), "ctx-form", "hi"),
        ],
        ids=["none", "empty", "one_key", "form", "reserved_keys"],
    )
    def test_context_data_matrix(
        self, template_context, expected_form, expected_value
    ) -> None:
        """The template context travels uncopied and the form is lifted out of it."""
        probe = DeferringProvider(None)
        r = DependencyResolver()
        r.prepend_provider(probe)

        result = r.resolve_with_template_context(
            _takes_probe_key, template_context=template_context
        )

        assert result == {"probe_key": expected_value}
        (context,) = probe.contexts
        assert context.context_data == (template_context or {})
        assert context.form == expected_form
        assert context.request is None
        assert context.url_kwargs == {}
        if template_context:
            assert context.context_data is template_context

    def test_a_reserved_key_stays_invisible_to_the_name_provider(self) -> None:
        def takes_reserved(cleaned_data: object = "unset") -> object:
            return cleaned_data

        result = DependencyResolver().resolve_with_template_context(
            takes_reserved, template_context={"cleaned_data": {"a": 1}}
        )
        assert result == {"cleaned_data": "unset"}


class TestDependencyCycleError:
    """Tests for circular dependency detection."""

    def test_self_cycle_raises(self) -> None:
        """When a dependency needs itself (a -> a), DependencyCycleError is raised."""

        def get_a(a: str = Depends("a")) -> str:
            return f"a-{a}"

        def top(a: str = Depends("a")) -> str:
            return a

        r = DependencyResolver()
        r.register_dependency("a", get_a)
        cache: dict = {}
        stack: list[str] = []
        with pytest.raises(DependencyCycleError) as exc_info:
            r.resolve_dependencies(top, request=None, _cache=cache, _stack=stack)
        cycle = exc_info.value.cycle
        assert "a" in cycle
        assert "Circular dependency" in str(exc_info.value)

    def test_resolve_callable_dependency_raises_when_name_not_registered(self) -> None:
        """Outside a resolve the message names the dependency alone."""
        ctx = _ctx()
        with pytest.raises(UnknownDependencyError) as exc_info:
            DependencyResolver()._resolve_callable_dependency("nonexistent", ctx)
        assert exc_info.value.param_name is None
        assert str(exc_info.value) == (
            'Depends("nonexistent") names a dependency nothing registered. '
            'Register it with resolver.dependency("nonexistent") or fix the name.'
        )

    def test_resolve_callable_dependency_raises_when_cache_has_in_progress(
        self,
    ) -> None:
        """When cache has name with _IN_PROGRESS but name not in stack, cycle is detected."""
        r = DependencyResolver()
        r.register_dependency("a", lambda: None)
        cache = {"a": _IN_PROGRESS_SENTINEL}
        stack = []
        ctx = _ctx(cache=cache, stack=stack)
        with pytest.raises(DependencyCycleError) as exc_info:
            r._resolve_callable_dependency("a", ctx)
        assert exc_info.value.cycle == ["a"]

    def test_cycle_a_depends_on_b_b_depends_on_a_raises(self) -> None:
        """When A needs B and B needs A, DependencyCycleError is raised."""

        def get_a(b: str = Depends("b")) -> str:
            return f"a-{b}"

        def get_b(a: str = Depends("a")) -> str:
            return f"b-{a}"

        def top(a: str = Depends("a")) -> str:
            return a

        r = DependencyResolver()
        r.register_dependency("a", get_a)
        r.register_dependency("b", get_b)
        cache: dict = {}
        stack: list = []
        with pytest.raises(DependencyCycleError) as exc_info:
            r.resolve_dependencies(top, request=None, _cache=cache, _stack=stack)
        cycle = exc_info.value.cycle
        assert "a" in cycle
        assert "b" in cycle
        assert "Circular dependency" in str(exc_info.value)


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
    """`provides` answers from the same plan `resolve` replays."""

    def test_terminal_answers_true_without_a_context_check(self) -> None:
        def fn(theme: str = Depends("theme")) -> None:
            return None

        param = inspect.signature(fn).parameters["theme"]
        assert DependencyResolver().provides(fn, param, make_resolution_context())

    def test_candidates_answer_from_the_context(self, mock_http_request) -> None:
        def fn(theme: HttpRequest | None = None) -> None:
            return None

        instance = DependencyResolver()
        param = inspect.signature(fn).parameters["theme"]
        assert instance.provides(fn, param, make_resolution_context()) is False
        with_request = make_resolution_context(request=mock_http_request())
        assert instance.provides(fn, param, with_request) is True

    def test_unclaimed_parameter_answers_false(self) -> None:
        def fn(entries) -> None:
            return None

        param = inspect.signature(fn).parameters["entries"]
        context = make_resolution_context()
        assert DependencyResolver().provides(fn, param, context) is False

    def test_parameter_outside_the_plan_answers_false(self) -> None:
        def fn(self, *args, value: int = 1) -> None:
            return None

        instance = DependencyResolver()
        skipped = inspect.signature(fn).parameters["args"]
        assert instance.provides(fn, skipped, make_resolution_context()) is False
        foreign = inspect_parameter("nowhere")
        assert instance.provides(fn, foreign, make_resolution_context()) is False

    def test_candidates_are_asked_about_the_parameter(self) -> None:
        def fn(plain) -> None:
            return None

        instance = DependencyResolver()
        stub = DeferringProvider("plain")
        instance.prepend_provider(stub)
        param = inspect.signature(fn).parameters["plain"]
        assert instance.provides(fn, param, make_resolution_context()) is True
        assert stub.seen == ["plain"]

    def test_candidates_see_the_resolved_annotation(self, mock_http_request) -> None:
        def by_stub(user_id: "int") -> None:
            return None

        def by_request(request: "HttpRequest") -> None:
            return None

        instance = DependencyResolver()
        stub = DeferringProvider("user_id")
        instance.prepend_provider(stub)
        seen: list[object] = []

        def can_handle(param, context, original=stub.can_handle) -> bool:
            seen.append(param.annotation)
            return original(param, context)

        stub.can_handle = can_handle
        param = inspect.signature(by_stub).parameters["user_id"]
        assert instance.provides(by_stub, param, make_resolution_context()) is True
        assert seen == [int]
        param = inspect.signature(by_request).parameters["request"]
        assert param.annotation == "HttpRequest"
        context = make_resolution_context(request=mock_http_request())
        assert instance.provides(by_request, param, context) is True


class TestBoundedCaches:
    """Every per-callable memo evicts its oldest entry past the bound."""

    @pytest.fixture()
    def _tiny_bound(self, monkeypatch) -> None:
        monkeypatch.setattr(_resolver_module, "_INTROSPECTION_CACHE_MAX_SIZE", 1)

    @pytest.mark.usefixtures("_tiny_bound")
    def test_introspection_memos_drop_the_oldest_entry(self) -> None:
        def first(a: int) -> None:
            return None

        def second(b: int) -> None:
            return None

        for memo in (_signature_cache, _type_hints_cache, _var_keyword_cache):
            memo.clear()
        cached_signature(first)
        cached_type_hints(first)
        cached_accepts_var_keyword(first)
        cached_signature(second)
        cached_type_hints(second)
        cached_accepts_var_keyword(second)
        for memo in (_signature_cache, _type_hints_cache, _var_keyword_cache):
            assert _introspect_key(first) not in memo
            assert _introspect_key(second) in memo

    @pytest.mark.usefixtures("_tiny_bound")
    def test_plan_cache_drops_the_oldest_entry(self) -> None:
        def first(a: int) -> None:
            return None

        def second(b: int) -> None:
            return None

        instance = _minimal_resolver()
        instance.resolve_dependencies(first)
        instance.resolve_dependencies(second)
        assert list(instance._plan_cache) == [_introspect_key(second)]
        assert instance.resolve_dependencies(first) == {"a": None}
        assert list(instance._plan_cache) == [_introspect_key(first)]


class _LegacyProvider:
    """Provider written against the two-method contract, missing the static hook."""

    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        return False

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        return None


@dataclass
class _UnhashableHandler:
    """Callable object no mapping can key, the way an `eq=True` dataclass is."""

    tag: str = "x"

    def __call__(self, plain: int = 3) -> int:
        return plain


@dataclass
class _UnhashableValue:
    """Unhashable object that is no callable either, so it has no signature."""

    tag: str = "x"


class _CatchUpLock:
    """Lock that lets the registry catch up in the moment before it is granted.

    Stands in for the resync another thread finished while this one waited.
    """

    def __init__(self, owner: DependencyResolver) -> None:
        self._owner = owner
        self._inner = threading.RLock()

    def __enter__(self) -> None:
        self._inner.acquire()
        self._owner._registry_seen = provider_registry.version

    def __exit__(self, *exc_info: object) -> None:
        self._inner.release()


class _MutatingProvider:
    """Provider that adds another one the first time the compiler asks it."""

    def __init__(self, owner: DependencyResolver) -> None:
        self._owner = owner
        self._fired = False

    def static_can_handle(self, param: inspect.Parameter) -> bool:
        if not self._fired:
            self._fired = True
            self._owner.add_provider(DeferringProvider("nothing"))
        return False

    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        return False

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        return None


def _deferred_request(request=None) -> object:
    return request


# A name nothing defines yet, so `get_type_hints` raises until it exists.
_deferred_request.__annotations__["request"] = "_LateRequest"


def _broken_annotation(value=1) -> object:
    return value


# A forward reference the parser refuses, which `get_type_hints` raises on.
_broken_annotation.__annotations__["value"] = "int |"


class TestProviderContract:
    """A provider joins a resolver only with a callable `static_can_handle`."""

    def test_the_constructor_refuses_a_provider_without_the_hook(self) -> None:
        with pytest.raises(TypeError, match="_LegacyProvider") as exc_info:
            DependencyResolver(_LegacyProvider())
        assert "static_can_handle" in str(exc_info.value)

    @pytest.mark.parametrize("entry", ["add_provider", "prepend_provider", "register"])
    def test_every_other_door_refuses_it_too(self, entry) -> None:
        instance = DependencyResolver()
        with pytest.raises(TypeError, match="_LegacyProvider"):
            getattr(instance, entry)(_LegacyProvider())

    def test_register_refuses_a_class_without_the_hook(self) -> None:
        instance = DependencyResolver()
        with pytest.raises(TypeError, match="_LegacyProvider"):
            instance.register(_LegacyProvider)

    def test_an_auto_registered_provider_is_refused_at_the_resync(self) -> None:
        class Legacy(RegisteredParameterProvider):
            static_can_handle = None

            def can_handle(self, param, context) -> bool:
                return False

            def resolve(self, param, context) -> object:
                return None

        def fn(a: int = 1) -> None:
            return None

        with pytest.raises(TypeError, match="Legacy"):
            DependencyResolver().resolve_dependencies(fn)


class TestUnhashableCallable:
    """A callable no mapping can key still resolves, only without a memo."""

    def test_resolve_compiles_a_plan_without_caching_it(self) -> None:
        instance = DependencyResolver()
        handler = _UnhashableHandler()
        assert instance.resolve_dependencies(handler) == {"plain": 3}
        assert instance._plan_cache == {}

    def test_provides_answers_from_a_fresh_plan(self) -> None:
        instance = DependencyResolver()
        handler = _UnhashableHandler()
        param = inspect.signature(handler).parameters["plain"]
        empty = make_resolution_context()
        filled = make_resolution_context(url_kwargs={"plain": 5})
        assert instance.provides(handler, param, empty) is False
        assert instance.provides(handler, param, filled) is True

    def test_an_unreadable_signature_falls_back_to_nothing(self) -> None:
        assert DependencyResolver().resolve_dependencies(_UnhashableValue()) == {}

    def test_the_memo_helpers_inspect_it_afresh(self) -> None:
        handler = _UnhashableHandler()
        assert list(cached_signature(handler).parameters) == ["plain"]
        assert cached_type_hints(handler) == {"tag": str}
        assert cached_accepts_var_keyword(handler) is False


class TestMemoRecency:
    """Every memo moves an entry it served to the fresh end, so the bound is LRU."""

    def test_a_hit_makes_the_introspection_entry_the_freshest(self) -> None:
        def first(a: int) -> None:
            return None

        def second(b: int) -> None:
            return None

        for memo, read in (
            (_signature_cache, cached_signature),
            (_type_hints_cache, cached_type_hints),
            (_var_keyword_cache, cached_accepts_var_keyword),
        ):
            memo.clear()
            read(first)
            read(second)
            assert list(memo)[-1] == _introspect_key(second)
            read(first)
            assert list(memo)[-1] == _introspect_key(first)

    def test_a_hit_makes_the_plan_the_freshest(self) -> None:
        def first(a: int) -> None:
            return None

        def second(b: int) -> None:
            return None

        instance = _minimal_resolver()
        instance.resolve_dependencies(first)
        instance.resolve_dependencies(second)
        assert list(instance._plan_cache)[-1] == _introspect_key(second)
        instance.resolve_dependencies(first)
        assert list(instance._plan_cache)[-1] == _introspect_key(first)


class TestProvisionalPlan:
    """Hints that do not resolve leave the plan out of the cache."""

    def test_a_plan_built_on_raw_annotations_is_recompiled(
        self, caplog, mock_http_request
    ) -> None:
        instance = DependencyResolver()
        request = mock_http_request()
        with caplog.at_level(logging.DEBUG, logger="next.deps.resolver"):
            assert instance.resolve_dependencies(
                _deferred_request, request=request
            ) == {"request": None}
        assert _introspect_key(_deferred_request) not in instance._plan_cache
        assert "did not resolve" in caplog.text

        globals()["_LateRequest"] = HttpRequest
        try:
            resolved = instance.resolve_dependencies(_deferred_request, request=request)
        finally:
            del globals()["_LateRequest"]
        assert resolved == {"request": request}
        assert _introspect_key(_deferred_request) in instance._plan_cache

    def test_an_annotation_the_parser_refuses_is_survived(self) -> None:
        instance = DependencyResolver()
        assert instance.resolve_dependencies(_broken_annotation) == {"value": 1}
        assert _introspect_key(_broken_annotation) not in instance._plan_cache


class TestPlanVersionStamp:
    """A plan carries the providers version the compile started from."""

    def test_a_change_during_the_compile_stamps_the_plan_stale(self) -> None:
        def fn(a: int = 1) -> None:
            return None

        instance = DependencyResolver()
        instance.prepend_provider(_MutatingProvider(instance))
        instance.resolve_dependencies(fn)
        stamped, _plan = instance._plan_cache[_introspect_key(fn)]
        assert stamped < instance._providers_version


class TestSyncLocking:
    """The resync holds a lock, so two threads never build the auto list twice."""

    def test_a_resync_finished_while_waiting_for_the_lock_is_not_repeated(self) -> None:
        instance = DependencyResolver()
        instance._lock = _CatchUpLock(instance)
        instance._sync_providers()
        assert instance._auto == []
        assert instance._providers_version == 0

    def test_two_threads_leave_one_auto_list(self) -> None:
        instance = DependencyResolver()
        barrier = threading.Barrier(2)

        def sync() -> None:
            barrier.wait()
            instance._sync_providers()

        threads = [threading.Thread(target=sync) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(instance._auto) == len(list(provider_registry))
        assert len(instance._providers) == len(instance._auto)


class TestForgetDepCaches:
    """A router reload drops every memo keyed by a callable."""

    def test_every_memo_is_emptied(self) -> None:
        def fn(a: int = 1) -> None:
            return None

        resolver.resolve_dependencies(fn)
        cached_accepts_var_keyword(fn)
        assert _signature_cache
        assert _type_hints_cache
        assert _var_keyword_cache
        assert resolver._plan_cache

        forget_dep_caches()

        assert not _signature_cache
        assert not _type_hints_cache
        assert not _var_keyword_cache
        assert not resolver._plan_cache
