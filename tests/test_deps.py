"""Tests for next.deps dependency resolution."""

import inspect
from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest

from next.deps import (
    _IN_PROGRESS,
    DEFAULT_PROVIDERS,
    CallableDependencyProvider,
    DependencyCycleError,
    Deps,
    FormProvider,
    HttpRequestProvider,
    RequestContext,
    UrlKwargsProvider,
    resolver,
)


class TestRequestContext:
    """Tests for RequestContext."""

    def test_default_url_kwargs_is_empty_dict(self) -> None:
        """Default context has empty url_kwargs and None request/form."""
        ctx = RequestContext()
        assert ctx.url_kwargs == {}
        assert ctx.request is None
        assert ctx.form is None

    def test_explicit_values(self) -> None:
        """Explicit request, form and url_kwargs are stored as given."""
        request = MagicMock(spec=HttpRequest)
        form = MagicMock()
        ctx = RequestContext(request=request, form=form, url_kwargs={"id": 1})
        assert ctx.request is request
        assert ctx.form is form
        assert ctx.url_kwargs == {"id": 1}


class TestHttpRequestProvider:
    """Tests for HttpRequestProvider."""

    def test_can_handle_when_annotation_is_http_request_and_request_present(
        self,
    ) -> None:
        """can_handle is True when param is HttpRequest and request is in context."""
        provider = HttpRequestProvider()
        param = inspect.Parameter(
            "request",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=HttpRequest,
        )
        ctx = RequestContext(request=MagicMock(spec=HttpRequest))
        assert provider.can_handle(param, ctx) is True

    def test_can_handle_false_when_request_is_none(self) -> None:
        """can_handle is False when context.request is None."""
        provider = HttpRequestProvider()
        param = inspect.Parameter(
            "request",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=HttpRequest,
        )
        ctx = RequestContext()
        assert provider.can_handle(param, ctx) is False

    def test_can_handle_false_when_annotation_empty(self) -> None:
        """can_handle is False when param has no annotation."""
        provider = HttpRequestProvider()
        param = inspect.Parameter(
            "request",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=inspect.Parameter.empty,
        )
        ctx = RequestContext(request=MagicMock(spec=HttpRequest))
        assert provider.can_handle(param, ctx) is False

    def test_resolve_returns_request(self) -> None:
        """Resolve returns context.request."""
        provider = HttpRequestProvider()
        request = MagicMock(spec=HttpRequest)
        param = inspect.Parameter(
            "request",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=HttpRequest,
        )
        ctx = RequestContext(request=request)
        assert provider.resolve(param, ctx) is request


class TestUrlKwargsProvider:
    """Tests for UrlKwargsProvider."""

    def test_can_handle_true_when_name_in_url_kwargs(self) -> None:
        """can_handle is True when param name is in url_kwargs."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "id",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=int,
        )
        ctx = RequestContext(url_kwargs={"id": 42})
        assert provider.can_handle(param, ctx) is True

    def test_can_handle_false_when_name_not_in_url_kwargs(self) -> None:
        """can_handle is False when param name is not in url_kwargs."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "id",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=int,
        )
        ctx = RequestContext(url_kwargs={})
        assert provider.can_handle(param, ctx) is False

    def test_resolve_returns_value_as_is_when_type_matches(self) -> None:
        """Resolve returns url_kwargs value unchanged when type matches annotation."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "id",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=int,
        )
        ctx = RequestContext(url_kwargs={"id": 42})
        assert provider.resolve(param, ctx) == 42

    def test_resolve_converts_str_to_int_when_annotation_int(self) -> None:
        """Resolve converts string to int when param is annotated as int."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "id",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=int,
        )
        ctx = RequestContext(url_kwargs={"id": "99"})
        assert provider.resolve(param, ctx) == 99

    def test_resolve_returns_value_unchanged_when_annotation_empty(self) -> None:
        """Resolve returns value as-is when param has no annotation."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "slug",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=inspect.Parameter.empty,
        )
        ctx = RequestContext(url_kwargs={"slug": "hello"})
        assert provider.resolve(param, ctx) == "hello"

    def test_resolve_returns_value_unchanged_when_int_conversion_fails(self) -> None:
        """Resolve returns original value when int() conversion fails."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "id",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=int,
        )
        ctx = RequestContext(url_kwargs={"id": "not-a-number"})
        assert provider.resolve(param, ctx) == "not-a-number"

    def test_resolve_splits_path_when_annotation_list_str(self) -> None:
        """Resolve splits path string into list of segments for list[str] ([[args]])."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "args",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=list[str],
        )
        ctx = RequestContext(url_kwargs={"args": "a/b/c"})
        assert provider.resolve(param, ctx) == ["a", "b", "c"]

    def test_resolve_list_str_empty_path_returns_empty_list(self) -> None:
        """Resolve returns [] for list[str] when path value is empty or only slashes."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "args",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=list[str],
        )
        ctx = RequestContext(url_kwargs={"args": ""})
        assert provider.resolve(param, ctx) == []


class TestFormProvider:
    """Tests for FormProvider."""

    def test_can_handle_true_when_param_name_is_form_and_form_present(self) -> None:
        """can_handle is True when param name is 'form' and form is in context."""
        provider = FormProvider()
        form = MagicMock()
        param = inspect.Parameter(
            "form",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=inspect.Parameter.empty,
        )
        ctx = RequestContext(form=form)
        assert provider.can_handle(param, ctx) is True

    def test_can_handle_false_when_form_is_none(self) -> None:
        """can_handle is False when context.form is None."""
        provider = FormProvider()
        param = inspect.Parameter(
            "form",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=inspect.Parameter.empty,
        )
        ctx = RequestContext()
        assert provider.can_handle(param, ctx) is False

    def test_can_handle_true_when_annotation_is_form_class_and_instance_matches(
        self,
    ) -> None:
        """can_handle is True when param annotation matches form instance type."""

        class MyForm:
            pass

        provider = FormProvider()
        form = MyForm()
        param = inspect.Parameter(
            "f",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=MyForm,
        )
        ctx = RequestContext(form=form)
        assert provider.can_handle(param, ctx) is True

    def test_can_handle_false_when_annotation_not_matching_instance(self) -> None:
        """can_handle is False when form instance type does not match annotation."""

        class FormA:
            pass

        class FormB:
            pass

        provider = FormProvider()
        param = inspect.Parameter(
            "f",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=FormB,
        )
        ctx = RequestContext(form=FormA())
        assert provider.can_handle(param, ctx) is False

    def test_can_handle_false_when_param_not_form_and_annotation_empty(self) -> None:
        """can_handle is False when param name is not 'form' and no annotation."""
        provider = FormProvider()
        param = inspect.Parameter(
            "other",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=inspect.Parameter.empty,
        )
        ctx = RequestContext(form=MagicMock())
        assert provider.can_handle(param, ctx) is False

    def test_resolve_returns_form(self) -> None:
        """Resolve returns context.form."""
        provider = FormProvider()
        form = MagicMock()
        param = inspect.Parameter(
            "form",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=inspect.Parameter.empty,
        )
        ctx = RequestContext(form=form)
        assert provider.resolve(param, ctx) is form


class TestDefaultDependencyResolver:
    """Tests for DefaultDependencyResolver."""

    def test_resolve_dependencies_injects_request_only(self) -> None:
        """Only request is injected when function has single request param."""

        def fn(request: HttpRequest) -> str:
            return getattr(request, "path", "")

        resolver = Deps(*DEFAULT_PROVIDERS)
        request = MagicMock(spec=HttpRequest)
        request.path = "/test/"
        result = resolver.resolve_dependencies(fn, request=request)
        assert result == {"request": request}

    def test_resolve_dependencies_injects_request_and_id(self) -> None:
        """Request and url_kwargs (id) are injected."""

        def fn(request: HttpRequest, id: int) -> None:  # noqa: A002
            pass

        resolver = Deps(*DEFAULT_PROVIDERS)
        request = MagicMock(spec=HttpRequest)
        result = resolver.resolve_dependencies(fn, request=request, id=42)
        assert result == {"request": request, "id": 42}

    def test_resolve_dependencies_injects_form(self) -> None:
        """Request and form are injected when both in context."""

        def fn(request: HttpRequest, form: MagicMock) -> None:
            pass

        resolver = Deps(*DEFAULT_PROVIDERS)
        request = MagicMock(spec=HttpRequest)
        form = MagicMock()
        result = resolver.resolve_dependencies(fn, request=request, form=form)
        assert result == {"request": request, "form": form}

    def test_resolve_dependencies_empty_signature_returns_empty(self) -> None:
        """Empty dict when function has no parameters."""

        def fn() -> None:
            pass

        resolver = Deps(*DEFAULT_PROVIDERS)
        result = resolver.resolve_dependencies(fn)
        assert result == {}

    def test_resolve_dependencies_skips_self(self) -> None:
        """'self' is not included in resolved dict for bound methods."""

        class C:
            def method(self, request: HttpRequest) -> None:
                pass

        resolver = Deps(*DEFAULT_PROVIDERS)
        request = MagicMock(spec=HttpRequest)
        result = resolver.resolve_dependencies(C.method, request=request)
        assert "self" not in result
        assert result == {"request": request}

    def test_resolve_dependencies_skips_cls(self) -> None:
        """'cls' is not included in resolved dict for classmethods."""

        class C:
            @classmethod
            def get_initial(cls, request: HttpRequest, id: int) -> dict:  # noqa: A002, ARG003
                return {}

        resolver = Deps(*DEFAULT_PROVIDERS)
        request = MagicMock(spec=HttpRequest)
        result = resolver.resolve_dependencies(C.get_initial, request=request, id=1)
        assert "cls" not in result
        assert result == {"request": request, "id": 1}

    def test_resolve_dependencies_unknown_param_without_default_gets_none(
        self,
    ) -> None:
        """Params with no provider and no context value get None."""

        def fn(unknown: str) -> None:
            pass

        resolver = Deps(*DEFAULT_PROVIDERS)
        result = resolver.resolve_dependencies(fn)
        assert result == {"unknown": None}

    def test_resolve_dependencies_skips_var_positional_and_var_keyword(
        self,
    ) -> None:
        """*args and **kwargs are not included in resolved dict."""

        def fn(request: HttpRequest, *args: object, **kwargs: object) -> None:
            pass

        resolver = Deps(*DEFAULT_PROVIDERS)
        request = MagicMock(spec=HttpRequest)
        result = resolver.resolve_dependencies(fn, request=request)
        assert result == {"request": request}
        assert "args" not in result
        assert "kwargs" not in result

    def test_resolve_dependencies_custom_providers(self) -> None:
        """Custom providers can supply values for arbitrary param names."""

        def fn(x: int) -> None:
            pass

        class CustomProvider:
            def can_handle(
                self, param: inspect.Parameter, context: RequestContext
            ) -> bool:
                return param.name == "x"

            def resolve(
                self, param: inspect.Parameter, context: RequestContext
            ) -> object:
                return 100

        resolver = Deps(CustomProvider())
        result = resolver.resolve_dependencies(fn)
        assert result == {"x": 100}

    def test_resolve_dependencies_invalid_signature_returns_empty(self) -> None:
        """Non-callable or invalid signature yields empty dict."""
        resolver = Deps(*DEFAULT_PROVIDERS)
        result = resolver.resolve_dependencies("not a callable")
        assert result == {}


class TestDepsConstruction:
    """Tests for Deps(*providers) and DEFAULT_PROVIDERS."""

    def test_deps_with_default_providers_has_same_order(self) -> None:
        """Deps(*DEFAULT_PROVIDERS) stores same providers in same order."""
        instance = Deps(*DEFAULT_PROVIDERS)
        assert len(instance._providers) == len(DEFAULT_PROVIDERS)
        for i, p in enumerate(DEFAULT_PROVIDERS):
            assert instance._providers[i] is p

    def test_deps_empty_has_no_providers(self) -> None:
        """Deps() with no args has empty _providers; unknown params get None."""
        instance = Deps()

        def fn(unknown: str) -> None:
            pass

        result = instance.resolve_dependencies(fn)
        assert result == {"unknown": None}


class TestDepsAddProvider:
    """Tests for add_provider on Deps."""

    def test_add_provider_appends_and_resolves(self) -> None:
        """After add_provider, custom param is resolved by added provider."""
        r = Deps(*DEFAULT_PROVIDERS)

        class CustomProvider:
            def can_handle(
                self, param: inspect.Parameter, context: RequestContext
            ) -> bool:
                return param.name == "x"

            def resolve(
                self, param: inspect.Parameter, context: RequestContext
            ) -> object:
                return 99

        r.add_provider(CustomProvider())

        def fn(x: int) -> None:
            pass

        result = r.resolve_dependencies(fn)
        assert result == {"x": 99}

    def test_context_key_provider_injects_from_context_data(self) -> None:
        """Param whose name is in _context_data gets value from ContextKeyProvider."""
        r = Deps(*DEFAULT_PROVIDERS)

        def fn(custom_context_var: str) -> str:
            return custom_context_var

        result = r.resolve_dependencies(
            fn, _context_data={"custom_context_var": "12345"}
        )
        assert result == {"custom_context_var": "12345"}
        assert fn(**result) == "12345"


class TestResolverRegister:
    """Tests for resolver.register decorator and method."""

    def test_register_decorator_adds_provider_class(self) -> None:
        """@resolver.register on a class registers an instance; next resolve uses it."""
        initial_count = len(resolver._providers)

        @resolver.register
        class InjectedProvider:
            def can_handle(
                self, param: inspect.Parameter, context: RequestContext
            ) -> bool:
                return param.name == "injected"

            def resolve(
                self, param: inspect.Parameter, context: RequestContext
            ) -> object:
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
        r = Deps(*DEFAULT_PROVIDERS)

        class MyProvider:
            def can_handle(
                self, param: inspect.Parameter, context: RequestContext
            ) -> bool:
                return param.name == "x"

            def resolve(
                self, param: inspect.Parameter, context: RequestContext
            ) -> object:
                return 42

        r.register(MyProvider())

        def fn(x: int) -> None:
            pass

        result = r.resolve_dependencies(fn)
        assert result == {"x": 42}


class TestResolverResolveDependencies:
    """Tests for resolver.resolve_dependencies (global resolver)."""

    def test_resolve_dependencies_returns_resolved_dict(self) -> None:
        """resolver.resolve_dependencies returns request when only request declared."""

        def fn(request: HttpRequest) -> None:
            pass

        request = MagicMock(spec=HttpRequest)
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


class TestRegisterDependency:
    """Tests for register_dependency and dependency decorator (callable dependencies)."""

    def test_register_dependency_resolves_param_by_name(self) -> None:
        """A param whose name is a registered dependency gets the callable result."""
        r = Deps(*DEFAULT_PROVIDERS)

        def get_user(request: HttpRequest) -> str:
            return "alice"

        r.register_dependency("current_user", get_user)
        request = MagicMock(spec=HttpRequest)

        def view(current_user: str) -> str:
            return current_user

        cache = {}
        result = r.resolve_dependencies(view, request=request, _cache=cache)
        assert result["current_user"] == "alice"
        assert view(**result) == "alice"

    def test_dependency_decorator_registers_callable(self) -> None:
        """@resolver.dependency('name') registers the function and returns it."""
        r = Deps(*DEFAULT_PROVIDERS)
        request = MagicMock(spec=HttpRequest)

        @r.dependency("product")
        def get_product(request: HttpRequest, id: int) -> str:  # noqa: A002
            return f"product-{id}"

        def page(product: str) -> str:
            return product

        cache = {}
        result = r.resolve_dependencies(page, request=request, id=3, _cache=cache)
        assert result["product"] == "product-3"

    def test_registered_dependency_not_used_if_url_kwargs_same_name(self) -> None:
        """URL kwargs take precedence: param 'id' gets url value, not a dependency."""
        r = Deps(*DEFAULT_PROVIDERS)

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

    def test_same_dependency_requested_twice_called_once_with_cache(self) -> None:
        """Two resolve_dependencies calls sharing _cache: dependency callable runs once."""
        r = Deps(*DEFAULT_PROVIDERS)
        call_count = 0

        def get_user(request: HttpRequest) -> str:
            nonlocal call_count
            call_count += 1
            return "alice"

        r.register_dependency("current_user", get_user)
        request = MagicMock(spec=HttpRequest)

        def view1(current_user: str) -> str:
            return current_user

        def view2(current_user: str) -> str:
            return current_user

        cache = {}
        stack = []
        result1 = r.resolve_dependencies(
            view1, request=request, _cache=cache, _stack=stack
        )
        result2 = r.resolve_dependencies(
            view2, request=request, _cache=cache, _stack=stack
        )
        assert result1["current_user"] == "alice"
        assert result2["current_user"] == "alice"
        assert call_count == 1
        assert cache.get("current_user") == "alice"

    def test_without_cache_dependency_called_each_resolve(self) -> None:
        """Without _cache, each resolve_dependencies call invokes the dependency."""
        r = Deps(*DEFAULT_PROVIDERS)
        call_count = 0

        def get_user(request: HttpRequest) -> str:
            nonlocal call_count
            call_count += 1
            return "alice"

        r.register_dependency("current_user", get_user)
        request = MagicMock(spec=HttpRequest)

        def view(current_user: str) -> str:
            return current_user

        r.resolve_dependencies(view, request=request)
        r.resolve_dependencies(view, request=request)
        assert call_count == 2


class TestDependencyCycleError:
    """Tests for circular dependency detection."""

    def test_self_cycle_raises(self) -> None:
        """When a dependency needs itself (a -> a), DependencyCycleError is raised."""
        r = Deps(*DEFAULT_PROVIDERS)

        def get_a(a: str) -> str:
            return f"a-{a}"

        r.register_dependency("a", get_a)

        def top(a: str) -> str:
            return a

        cache = {}
        stack: list[str] = []
        with pytest.raises(DependencyCycleError) as exc_info:
            r.resolve_dependencies(top, request=None, _cache=cache, _stack=stack)
        cycle = exc_info.value.cycle
        assert "a" in cycle
        assert "Circular dependency" in str(exc_info.value)

    def test_cycle_a_depends_on_b_b_depends_on_a_raises(self) -> None:
        """When A needs B and B needs A, DependencyCycleError is raised."""
        r = Deps(*DEFAULT_PROVIDERS)

        def get_a(b: str) -> str:
            return f"a-{b}"

        def get_b(a: str) -> str:
            return f"b-{a}"

        r.register_dependency("a", get_a)
        r.register_dependency("b", get_b)

        def top(a: str) -> str:
            return a

        cache = {}
        stack = []
        with pytest.raises(DependencyCycleError) as exc_info:
            r.resolve_dependencies(top, request=None, _cache=cache, _stack=stack)
        cycle = exc_info.value.cycle
        assert "a" in cycle
        assert "b" in cycle
        assert "Circular dependency" in str(exc_info.value)

    def test_cycle_detected_via_cache_in_progress_sentinel(self) -> None:
        """When cache has name with _IN_PROGRESS, DependencyCycleError is raised."""
        r = Deps(*DEFAULT_PROVIDERS)
        r.register_dependency("a", lambda: None)
        provider = CallableDependencyProvider(r)
        param = inspect.Parameter(
            "a", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str
        )
        ctx = RequestContext(
            cache={"a": _IN_PROGRESS},
            stack=[],
            resolver=r,
        )
        with pytest.raises(DependencyCycleError) as exc_info:
            provider.resolve(param, ctx)
        assert exc_info.value.cycle == ["a"]

    def test_resolve_returns_none_when_resolver_is_none_no_cache(self) -> None:
        """When resolver is None and no cache, resolve returns None."""
        r = Deps(*DEFAULT_PROVIDERS)
        r.register_dependency("a", lambda: None)
        provider = CallableDependencyProvider(r)
        param = inspect.Parameter(
            "a", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str
        )
        ctx = RequestContext(resolver=None, cache=None, stack=None)
        assert provider.resolve(param, ctx) is None
