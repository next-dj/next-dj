import importlib.util
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest

from next.deps import (
    _IN_PROGRESS,
    DependencyCache,
    DependencyCycleError,
    DependencyResolver,
    Depends,
    DependsProvider,
    ProviderRegistry,
    RegisteredParameterProvider,
    resolver,
)
from next.forms import DForm, FormProvider
from next.pages import ContextByNameProvider
from next.urls import (
    DUrl,
    HttpRequestProvider,
    UrlByAnnotationProvider,
    UrlKwargsProvider,
    _coerce_url_value,
)
from tests.support import (
    COERCE_URL_VALUE_CASES,
    URL_BY_ANNOTATION_RESOLVE_CASES,
    URL_KWARGS_RESOLVE_CASES,
    CoerceUrlValueCase,
    UrlByAnnotationResolveCase,
    UrlKwargsResolveCase,
    _ctx,
    _minimal_resolver,
    _resolver_with_form,
    build_mock_http_request,
    inspect_parameter,
)


# For coverage of _resolve_callable_dependency edge cases
_IN_PROGRESS_SENTINEL = _IN_PROGRESS


def _mock_request_factory() -> MagicMock:
    return build_mock_http_request()


def _no_request() -> None:
    return None


class TestResolverDescriptor:
    """Test that providers can access resolver via descriptor when no __init__(resolver)."""

    def test_context_provider_resolver_attribute_returns_singleton(self) -> None:
        """ContextByNameProvider has no resolver in ``__init__``. ``self.resolver`` returns the global resolver."""
        provider = ContextByNameProvider()
        assert provider.resolver is resolver


class TestDynamicContext:
    """Tests for dynamic context (SimpleNamespace) used in resolve_dependencies."""

    def test_context_has_url_kwargs_and_request_form(self, mock_http_request) -> None:
        """Context can hold request, form, url_kwargs."""
        request = mock_http_request()
        form = MagicMock()
        ctx = _ctx(request=request, form=form, id=1)
        assert ctx.request is request
        assert ctx.form is form
        assert ctx.url_kwargs == {"id": 1}

    def test_default_context_has_empty_url_kwargs(self) -> None:
        """Default context has empty url_kwargs and None request/form."""
        ctx = _ctx()
        assert ctx.url_kwargs == {}
        assert ctx.request is None
        assert ctx.form is None


class TestHttpRequestProvider:
    """Tests for HttpRequestProvider (from next.urls)."""

    @pytest.mark.parametrize(
        ("request_obj", "annotation", "expected"),
        [
            (_mock_request_factory, HttpRequest, True),
            (_no_request, HttpRequest, False),
            (_mock_request_factory, inspect.Parameter.empty, False),
        ],
        ids=["request_present", "request_none", "annotation_empty"],
    )
    def test_can_handle(self, request_obj, annotation, expected) -> None:
        """can_handle matches request presence and HttpRequest annotation."""
        provider = HttpRequestProvider()
        param = inspect_parameter("request", annotation)
        req = request_obj()
        ctx = _ctx(request=req)
        assert provider.can_handle(param, ctx) is expected

    def test_resolve_returns_request(self, mock_http_request) -> None:
        """Resolve returns context.request."""
        provider = HttpRequestProvider()
        request = mock_http_request()
        param = inspect_parameter("request", HttpRequest)
        ctx = _ctx(request=request)
        assert provider.resolve(param, ctx) is request

    def test_can_handle_when_get_type_hints_raises_falls_back(
        self, tmp_path: Path
    ) -> None:
        """If ``get_type_hints`` raises, the except branch runs and fallback applies."""
        mod_path = tmp_path / "bad_ann.py"
        mod_path.write_text(
            "from __future__ import annotations\n"
            "def handler(request: DoesNotExist):\n"
            "    pass\n",
            encoding="utf-8",
        )
        spec = importlib.util.spec_from_file_location("bad_ann", mod_path)
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        handler = mod.handler

        provider = HttpRequestProvider()
        req = HttpRequest()
        ctx = _ctx(request=req)
        param = inspect.signature(handler).parameters["request"]
        resolver._resolve_call_stack.append(handler)
        try:
            assert provider.can_handle(param, ctx) is False
        finally:
            resolver._resolve_call_stack.pop()


class TestUrlKwargsProvider:
    """Tests for UrlKwargsProvider (from next.urls)."""

    @pytest.mark.parametrize(
        ("url_kwargs", "expected"),
        [
            ({"id": 42}, True),
            ({}, False),
        ],
        ids=["name_in_kwargs", "name_missing"],
    )
    def test_can_handle(self, url_kwargs, expected) -> None:
        """can_handle is True exactly when param name appears in url_kwargs."""
        provider = UrlKwargsProvider()
        param = inspect_parameter("id", int)
        ctx = _ctx(url_kwargs=url_kwargs)
        assert provider.can_handle(param, ctx) is expected

    @pytest.mark.parametrize(
        "case", URL_KWARGS_RESOLVE_CASES, ids=[c.id for c in URL_KWARGS_RESOLVE_CASES]
    )
    def test_resolve(self, case: UrlKwargsResolveCase) -> None:
        """Resolve applies annotation coercion and missing key rules."""
        provider = UrlKwargsProvider()
        param = inspect_parameter(case.name, case.annotation)
        ctx = _ctx(url_kwargs=case.url_kwargs)
        assert provider.resolve(param, ctx) == case.expected


class TestCoerceUrlValue:
    """Tests for _coerce_url_value (next.urls)."""

    @pytest.mark.parametrize(
        "case", COERCE_URL_VALUE_CASES, ids=[c.id for c in COERCE_URL_VALUE_CASES]
    )
    def test_coerce(self, case: CoerceUrlValueCase) -> None:
        """Coercion follows int, bool, float, and str rules."""
        assert _coerce_url_value(case.raw, case.hint) == case.expected


class TestUrlByAnnotationProvider:
    """Tests for UrlByAnnotationProvider (from next.urls)."""

    @pytest.mark.parametrize(
        ("annotation", "expected"),
        [
            (DUrl[int], True),
            (int, False),
        ],
        ids=["durl", "plain_int"],
    )
    def test_can_handle(self, annotation, expected) -> None:
        """can_handle is True only for DUrl annotations."""
        provider = UrlByAnnotationProvider()
        param = inspect_parameter("id", annotation)
        ctx = _ctx()
        assert provider.can_handle(param, ctx) is expected

    @pytest.mark.parametrize(
        "case",
        URL_BY_ANNOTATION_RESOLVE_CASES,
        ids=[c.id for c in URL_BY_ANNOTATION_RESOLVE_CASES],
    )
    def test_resolve(self, case: UrlByAnnotationResolveCase) -> None:
        """Resolve reads url_kwargs by param name and coerces via DUrl."""
        provider = UrlByAnnotationProvider()
        param = inspect_parameter(case.name, case.annotation)
        ctx = _ctx(url_kwargs=case.url_kwargs)
        assert provider.resolve(param, ctx) == case.expected


class TestFormProvider:
    """Tests for FormProvider (from next.forms)."""

    @pytest.mark.parametrize(
        ("param_name", "form_kind", "expected"),
        [
            ("form", "mock", True),
            ("form", "none", False),
            ("other", "mock", False),
        ],
        ids=["name_form_with_instance", "form_none", "wrong_name"],
    )
    def test_can_handle_basic(self, param_name, form_kind, expected) -> None:
        """can_handle for name 'form', missing form, and non-form param names."""
        provider = FormProvider()
        param = inspect_parameter(param_name, inspect.Parameter.empty)
        form = None if form_kind == "none" else MagicMock()
        ctx = _ctx(form=form)
        assert provider.can_handle(param, ctx) is expected

    def test_can_handle_true_when_annotation_is_form_class_and_instance_matches(
        self,
    ) -> None:
        """can_handle is True when param annotation matches form instance type."""

        class MyForm:
            pass

        provider = FormProvider()
        form = MyForm()
        param = inspect_parameter("f", MyForm)
        ctx = _ctx(form=form)
        assert provider.can_handle(param, ctx) is True

    def test_can_handle_false_when_annotation_not_matching_instance(self) -> None:
        """can_handle is False when form instance type does not match annotation."""

        class FormA:
            pass

        class FormB:
            pass

        provider = FormProvider()
        param = inspect_parameter("f", FormB)
        ctx = _ctx(form=FormA())
        assert provider.can_handle(param, ctx) is False

    def test_can_handle_true_when_annotation_is_dform_and_form_matches(self) -> None:
        """can_handle is True when param is DForm[FormClass] and form is that class."""
        provider = FormProvider()

        class MyForm:
            pass

        form = MyForm()
        param = inspect_parameter("f", DForm[MyForm])
        ctx = _ctx(form=form)
        assert provider.can_handle(param, ctx) is True

    def test_can_handle_false_when_annotation_is_dform_but_form_mismatch(self) -> None:
        """can_handle is False when param is DForm[FormB] but form is FormA."""

        class FormA:
            pass

        class FormB:
            pass

        provider = FormProvider()
        param = inspect_parameter("f", DForm[FormB])
        ctx = _ctx(form=FormA())
        assert provider.can_handle(param, ctx) is False

    def test_resolve_returns_form(self) -> None:
        """Resolve returns context.form."""
        provider = FormProvider()
        form = MagicMock()
        param = inspect_parameter("form", inspect.Parameter.empty)
        ctx = _ctx(form=form)
        assert provider.resolve(param, ctx) is form


class TestDependencyResolver:
    """Tests for DependencyResolver (single class, no Deps/DefaultDependencyResolver)."""

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
            def get_initial(cls, request: HttpRequest, id: int) -> dict:  # noqa: A002, ARG003
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


class TestDependencyCache:
    """Direct tests for DependencyCache (get sentinel, len, contains)."""

    def test_get_returns_in_progress_when_key_marked(self) -> None:
        """Get returns _IN_PROGRESS while key is in the in-progress set."""
        cache = DependencyCache()
        cache.mark_in_progress("dep")
        assert cache.get("dep") is _IN_PROGRESS
        cache.set("dep", "done")
        assert cache.get("dep") == "done"

    def test_len_and_contains_use_backing_cache(self) -> None:
        """__len__ and __contain__ reflect stored values, not in-progress markers."""
        cache = DependencyCache()
        assert len(cache) == 0
        assert "x" not in cache
        cache.set("x", 1)
        assert len(cache) == 1
        assert "x" in cache

    def test_is_in_progress_and_unmark(self) -> None:
        """The ``is_in_progress`` flag reflects ``mark``. ``unmark`` clears it."""
        cache = DependencyCache()
        cache.mark_in_progress("k")
        assert cache.is_in_progress("k")
        cache.unmark_in_progress("k")
        assert not cache.is_in_progress("k")


class TestProviderRegistry:
    """Tests for ProviderRegistry."""

    def test_register_get_providers_clear_len_iter(self) -> None:
        """Register providers, iterate, clear empties registry."""
        reg = ProviderRegistry()
        p1 = HttpRequestProvider()
        p2 = UrlKwargsProvider()
        reg.register(p1)
        reg.register(p2)
        assert len(reg) == 2
        assert reg.get_providers() == (p1, p2)
        assert list(iter(reg)) == [p1, p2]
        reg.clear()
        assert len(reg) == 0
        assert reg.get_providers() == ()


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


class TestRegisterDependency:
    """Tests for register_dependency and dependency decorator (callable dependencies)."""

    def test_register_dependency_resolves_param_by_name(
        self, mock_http_request
    ) -> None:
        """A param whose name is a registered dependency gets the callable result."""

        def get_user(request: HttpRequest) -> str:
            return "alice"

        resolver.register_dependency("current_user", get_user)
        try:
            request = mock_http_request()

            def view(current_user: str = Depends("current_user")) -> str:
                return current_user

            cache = {}
            stack = []
            result = resolver.resolve_dependencies(
                view, request=request, _cache=cache, _stack=stack
            )
            assert result["current_user"] == "alice"
            assert view(**result) == "alice"
        finally:
            resolver._dependency_callables.pop("current_user", None)

    def test_dependency_decorator_registers_callable(self, mock_http_request) -> None:
        """@resolver.dependency('name') registers the function and returns it."""
        request = mock_http_request()

        @resolver.dependency("product")
        def get_product(request: HttpRequest, id: int) -> str:  # noqa: A002
            return f"product-{id}"

        def page(product: str = Depends("product")) -> str:
            return product

        try:
            cache = {}
            stack = []
            result = resolver.resolve_dependencies(
                page, request=request, id=3, _cache=cache, _stack=stack
            )
            assert result["product"] == "product-3"
        finally:
            resolver._dependency_callables.pop("product", None)

    def test_depends_callable_is_called_with_di_resolved_args(
        self, mock_http_request
    ) -> None:
        """Depends(callable) resolves callable args and calls it."""

        def build_value(request: HttpRequest, id: int) -> str:  # noqa: A002
            return f"{getattr(request, 'path', '')}:{id}"

        def view(value: str = Depends(build_value)) -> str:
            return value

        r = DependencyResolver()
        request = mock_http_request(path="/x/")
        resolved = r.resolve_dependencies(view, request=request, id=7)
        assert resolved["value"] == "/x/:7"
        assert view(**resolved) == "/x/:7"

    def test_depends_constant_value_injects_as_is(self) -> None:
        """Depends(value) injects a constant value when not str/callable."""

        def view(x: int = Depends(123)) -> int:
            return x

        r = DependencyResolver()
        resolved = r.resolve_dependencies(view)
        assert resolved["x"] == 123

    def test_depends_without_args_uses_param_name(self) -> None:
        """Depends() resolves by parameter name (Depends("param_name"))."""
        r = DependencyResolver()
        r.register_dependency("current_user", lambda: "alice")
        try:

            def view(current_user: str = Depends()) -> str:
                return current_user

            resolved = r.resolve_dependencies(view)
            assert resolved["current_user"] == "alice"
        finally:
            r._dependency_callables.pop("current_user", None)

    def test_unregistered_depends_name_returns_none(self) -> None:
        """When Depends("name") is used but name is not registered, param gets None."""

        def view(foo: object = Depends("nonexistent")) -> str:
            return str(foo)

        result = resolver.resolve_dependencies(view)
        assert result["foo"] is None

    def test_depends_provider_resolve_returns_none_when_default_not_depends(
        self,
    ) -> None:
        """Defensive: DependsProvider.resolve returns None when default isn't Depends."""
        provider = DependsProvider(DependencyResolver())
        param = inspect_parameter("x", int, default=123)
        ctx = _ctx()
        assert provider.resolve(param, ctx) is None

    def test_registered_dependency_not_used_if_url_kwargs_same_name(self) -> None:
        """URL kwargs take precedence: param 'id' gets url value, not a dependency."""
        r = _minimal_resolver()

        @r.dependency("id")
        def get_id() -> int:
            return 999

        def fn(id: int) -> int:  # noqa: A002
            return id

        cache = {}
        result = r.resolve_dependencies(fn, id=42, _cache=cache)
        assert result["id"] == 42


class TestCallableDependencyCache:
    """Tests for request-scoped caching of dependency callable results."""

    def test_same_dependency_requested_twice_called_once_with_cache(
        self, mock_http_request
    ) -> None:
        """Two resolve_dependencies calls sharing _cache: dependency callable runs once."""
        call_count = 0

        def get_user(request: HttpRequest) -> str:
            nonlocal call_count
            call_count += 1
            return "alice"

        resolver.register_dependency("current_user", get_user)
        try:
            request = mock_http_request()

            def view1(current_user: str = Depends("current_user")) -> str:
                return current_user

            def view2(current_user: str = Depends("current_user")) -> str:
                return current_user

            cache = {}
            stack = []
            result1 = resolver.resolve_dependencies(
                view1, request=request, _cache=cache, _stack=stack
            )
            result2 = resolver.resolve_dependencies(
                view2, request=request, _cache=cache, _stack=stack
            )
            assert result1["current_user"] == "alice"
            assert result2["current_user"] == "alice"
            assert call_count == 1
            assert cache.get("current_user") == "alice"
        finally:
            resolver._dependency_callables.pop("current_user", None)

    def test_without_cache_dependency_called_each_resolve(
        self, mock_http_request
    ) -> None:
        """Without _cache, each resolve_dependencies call invokes the dependency."""
        call_count = 0

        def get_user(request: HttpRequest) -> str:
            nonlocal call_count
            call_count += 1
            return "alice"

        resolver.register_dependency("current_user", get_user)
        try:
            request = mock_http_request()

            def view(current_user: str = Depends("current_user")) -> str:
                return current_user

            resolver.resolve_dependencies(view, request=request)
            resolver.resolve_dependencies(view, request=request)
            assert call_count == 2
        finally:
            resolver._dependency_callables.pop("current_user", None)


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

            cache = {}
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
            stack = []  # Name not in stack. We hit the cache[_IN_PROGRESS] branch.
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

            cache = {}
            stack = []
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
