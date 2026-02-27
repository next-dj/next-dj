"""Tests for next.deps dependency resolution."""

import inspect
import types
from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest

from next.deps import (
    _IN_PROGRESS,
    DependencyCycleError,
    DependencyResolver,
    Depends,
    DependsProvider,
    RegisteredParameterProvider,
    resolver,
)
from next.forms import DForm, FormProvider
from next.urls import (
    DUrl,
    HttpRequestProvider,
    UrlByAnnotationProvider,
    UrlKwargsProvider,
    _coerce_url_value,
)


# For coverage of _resolve_callable_dependency edge cases
_IN_PROGRESS_SENTINEL = _IN_PROGRESS


def _ctx(
    request=None,
    form=None,
    url_kwargs=None,
    context_data=None,
    cache=None,
    stack=None,
    resolver_inst=None,
    _context_data=None,
    **kwargs: object,
) -> types.SimpleNamespace:
    """Build dynamic context (SimpleNamespace) for provider tests."""
    if url_kwargs is None:
        reserved = {
            "request",
            "form",
            "context_data",
            "cache",
            "stack",
            "resolver",
            "_context_data",
        }
        url_kwargs = {k: v for k, v in kwargs.items() if k not in reserved}
    return types.SimpleNamespace(
        request=request,
        form=form,
        url_kwargs=url_kwargs or {},
        context_data=context_data or _context_data or {},
        cache=cache,
        stack=stack,
        resolver=resolver_inst,
    )


def _minimal_resolver() -> DependencyResolver:
    """Return a resolver with only HttpRequest and URL providers (for isolated tests)."""
    return DependencyResolver(HttpRequestProvider(), UrlKwargsProvider())


def _resolver_with_form() -> DependencyResolver:
    """Return a resolver with request, URL and form providers."""
    return DependencyResolver(
        HttpRequestProvider(),
        UrlKwargsProvider(),
        FormProvider(),
    )


def _full_resolver() -> DependencyResolver:
    """Return a resolver with all auto-registered providers (for callable dependency tests)."""
    return DependencyResolver()


class TestResolverDescriptor:
    """Test that providers can access resolver via descriptor when no __init__(resolver)."""

    def test_context_provider_resolver_attribute_returns_singleton(self) -> None:
        """ContextByNameProvider has no resolver in __init__; self.resolver returns global."""
        from next.pages import ContextByNameProvider  # noqa: PLC0415

        provider = ContextByNameProvider()
        assert provider.resolver is resolver


class TestDynamicContext:
    """Tests for dynamic context (SimpleNamespace) used in resolve_dependencies."""

    def test_context_has_url_kwargs_and_request_form(self) -> None:
        """Context can hold request, form, url_kwargs."""
        request = MagicMock(spec=HttpRequest)
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
        ctx = _ctx(request=MagicMock(spec=HttpRequest))
        assert provider.can_handle(param, ctx) is True

    def test_can_handle_false_when_request_is_none(self) -> None:
        """can_handle is False when context.request is None."""
        provider = HttpRequestProvider()
        param = inspect.Parameter(
            "request",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=HttpRequest,
        )
        ctx = _ctx()
        assert provider.can_handle(param, ctx) is False

    def test_can_handle_false_when_annotation_empty(self) -> None:
        """can_handle is False when param has no annotation."""
        provider = HttpRequestProvider()
        param = inspect.Parameter(
            "request",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=inspect.Parameter.empty,
        )
        ctx = _ctx(request=MagicMock(spec=HttpRequest))
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
        ctx = _ctx(request=request)
        assert provider.resolve(param, ctx) is request


class TestUrlKwargsProvider:
    """Tests for UrlKwargsProvider (from next.urls)."""

    def test_can_handle_true_when_name_in_url_kwargs(self) -> None:
        """can_handle is True when param name is in url_kwargs."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "id",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=int,
        )
        ctx = _ctx(url_kwargs={"id": 42})
        assert provider.can_handle(param, ctx) is True

    def test_can_handle_false_when_name_not_in_url_kwargs(self) -> None:
        """can_handle is False when param name is not in url_kwargs."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "id",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=int,
        )
        ctx = _ctx()
        assert provider.can_handle(param, ctx) is False

    def test_resolve_returns_value_as_is_when_type_matches(self) -> None:
        """Resolve returns url_kwargs value unchanged when type matches annotation."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "id",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=int,
        )
        ctx = _ctx(url_kwargs={"id": 42})
        assert provider.resolve(param, ctx) == 42

    def test_resolve_converts_str_to_int_when_annotation_int(self) -> None:
        """Resolve converts string to int when param is annotated as int."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "id",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=int,
        )
        ctx = _ctx(url_kwargs={"id": "99"})
        assert provider.resolve(param, ctx) == 99

    def test_resolve_returns_value_unchanged_when_annotation_empty(self) -> None:
        """Resolve returns value as-is when param has no annotation."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "slug",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=inspect.Parameter.empty,
        )
        ctx = _ctx(url_kwargs={"slug": "hello"})
        assert provider.resolve(param, ctx) == "hello"

    def test_resolve_returns_value_unchanged_when_int_conversion_fails(self) -> None:
        """Resolve returns original value when int() conversion fails."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "id",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=int,
        )
        ctx = _ctx(url_kwargs={"id": "not-a-number"})
        assert provider.resolve(param, ctx) == "not-a-number"

    def test_resolve_returns_str_when_annotation_is_str(self) -> None:
        """Resolve returns str(raw) when param is annotated as str."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "slug",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=str,
        )
        ctx = _ctx(url_kwargs={"slug": "hello-world"})
        assert provider.resolve(param, ctx) == "hello-world"

    def test_resolve_returns_none_when_param_name_not_in_url_kwargs(self) -> None:
        """Resolve returns None when param name is not in url_kwargs."""
        provider = UrlKwargsProvider()
        param = inspect.Parameter(
            "missing",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=str,
        )
        ctx = _ctx(url_kwargs={"other": "value"})
        assert provider.resolve(param, ctx) is None


class TestCoerceUrlValue:
    """Tests for _coerce_url_value (next.urls)."""

    def test_coerce_int_valid(self) -> None:
        """Valid int string is converted to int."""
        assert _coerce_url_value("42", int) == 42

    def test_coerce_int_invalid_returns_unchanged(self) -> None:
        """Invalid int string is returned unchanged."""
        assert _coerce_url_value("x", int) == "x"

    def test_coerce_bool_true(self) -> None:
        """Bool hint: '1', 'true', 'yes' are True."""
        assert _coerce_url_value("true", bool) is True
        assert _coerce_url_value("1", bool) is True
        assert _coerce_url_value("yes", bool) is True

    def test_coerce_bool_false(self) -> None:
        """Bool hint: other values are False."""
        assert _coerce_url_value("0", bool) is False
        assert _coerce_url_value("false", bool) is False

    def test_coerce_float_valid(self) -> None:
        """Valid float string is converted to float."""
        assert _coerce_url_value("3.14", float) == 3.14

    def test_coerce_float_invalid_returns_unchanged(self) -> None:
        """Invalid float string is returned unchanged."""
        assert _coerce_url_value("x", float) == "x"

    def test_coerce_str_returns_unchanged(self) -> None:
        """Str or other hint returns value unchanged."""
        assert _coerce_url_value("hello", str) == "hello"


class TestUrlByAnnotationProvider:
    """Tests for UrlByAnnotationProvider (from next.urls)."""

    def test_can_handle_true_when_annotation_is_durl(self) -> None:
        """can_handle is True when param is annotated with DUrl."""
        provider = UrlByAnnotationProvider()
        param = inspect.Parameter(
            "id",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=DUrl[int],
        )
        ctx = _ctx()
        assert provider.can_handle(param, ctx) is True

    def test_can_handle_false_when_annotation_not_durl(self) -> None:
        """can_handle is False when param is not DUrl."""
        provider = UrlByAnnotationProvider()
        param = inspect.Parameter(
            "id",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=int,
        )
        ctx = _ctx()
        assert provider.can_handle(param, ctx) is False

    def test_resolve_returns_value_from_url_kwargs_by_key(self) -> None:
        """Resolve returns url_kwargs value coerced by DUrl type (e.g. DUrl[int])."""
        provider = UrlByAnnotationProvider()
        param = inspect.Parameter(
            "pk",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=DUrl[int],
        )
        ctx = _ctx(url_kwargs={"pk": "123"})
        assert provider.resolve(param, ctx) == 123

    def test_resolve_returns_value_by_param_name_when_key_not_str(self) -> None:
        """Resolve uses param.name when DUrl arg is not a string."""
        provider = UrlByAnnotationProvider()
        param = inspect.Parameter(
            "slug",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=DUrl[str],
        )
        ctx = _ctx(url_kwargs={"slug": "hello"})
        assert provider.resolve(param, ctx) == "hello"

    def test_resolve_returns_none_when_key_missing(self) -> None:
        """Resolve returns None when key is not in url_kwargs."""
        provider = UrlByAnnotationProvider()
        param = inspect.Parameter(
            "missing",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=DUrl[str],
        )
        ctx = _ctx(url_kwargs={})
        assert provider.resolve(param, ctx) is None


class TestFormProvider:
    """Tests for FormProvider (from next.forms)."""

    def test_can_handle_true_when_param_name_is_form_and_form_present(self) -> None:
        """can_handle is True when param name is 'form' and form is in context."""
        provider = FormProvider()
        form = MagicMock()
        param = inspect.Parameter(
            "form",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=inspect.Parameter.empty,
        )
        ctx = _ctx(form=form)
        assert provider.can_handle(param, ctx) is True

    def test_can_handle_false_when_form_is_none(self) -> None:
        """can_handle is False when context.form is None."""
        provider = FormProvider()
        param = inspect.Parameter(
            "form",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=inspect.Parameter.empty,
        )
        ctx = _ctx()
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
        ctx = _ctx(form=form)
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
        ctx = _ctx(form=FormA())
        assert provider.can_handle(param, ctx) is False

    def test_can_handle_false_when_annotation_empty_and_name_not_form(self) -> None:
        """can_handle is False when param has no annotation and name is not 'form'."""
        provider = FormProvider()
        form = MagicMock()
        param = inspect.Parameter(
            "other",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=inspect.Parameter.empty,
        )
        ctx = _ctx(form=form)
        assert provider.can_handle(param, ctx) is False

    def test_can_handle_true_when_annotation_is_dform_and_form_matches(self) -> None:
        """can_handle is True when param is DForm[FormClass] and form is that class."""
        provider = FormProvider()

        class MyForm:
            pass

        form = MyForm()
        param = inspect.Parameter(
            "f",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=DForm[MyForm],
        )
        ctx = _ctx(form=form)
        assert provider.can_handle(param, ctx) is True

    def test_can_handle_false_when_annotation_is_dform_but_form_mismatch(self) -> None:
        """can_handle is False when param is DForm[FormB] but form is FormA."""

        class FormA:
            pass

        class FormB:
            pass

        provider = FormProvider()
        param = inspect.Parameter(
            "f",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=DForm[FormB],
        )
        ctx = _ctx(form=FormA())
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
        ctx = _ctx(form=form)
        assert provider.resolve(param, ctx) is form


class TestDependencyResolver:
    """Tests for DependencyResolver (single class, no Deps/DefaultDependencyResolver)."""

    def test_resolve_dependencies_injects_request_only(self) -> None:
        """Only request is injected when function has single request param."""

        def fn(request: HttpRequest) -> str:
            return getattr(request, "path", "")

        r = _minimal_resolver()
        request = MagicMock(spec=HttpRequest)
        request.path = "/test/"
        result = r.resolve_dependencies(fn, request=request)
        assert result == {"request": request}

    def test_resolve_dependencies_injects_request_and_id(self) -> None:
        """Request and url_kwargs (id) are injected."""

        def fn(request: HttpRequest, id: int) -> None:  # noqa: A002
            pass

        r = _minimal_resolver()
        request = MagicMock(spec=HttpRequest)
        result = r.resolve_dependencies(fn, request=request, id=42)
        assert result == {"request": request, "id": 42}

    def test_resolve_dependencies_injects_form(self) -> None:
        """Request and form are injected when both in context."""

        def fn(request: HttpRequest, form: MagicMock) -> None:
            pass

        r = _resolver_with_form()
        request = MagicMock(spec=HttpRequest)
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

    def test_resolve_dependencies_skips_self(self) -> None:
        """'self' is not included in resolved dict for bound methods."""

        class C:
            def method(self, request: HttpRequest) -> None:
                pass

        r = _minimal_resolver()
        request = MagicMock(spec=HttpRequest)
        result = r.resolve_dependencies(C.method, request=request)
        assert "self" not in result
        assert result == {"request": request}

    def test_resolve_dependencies_skips_cls(self) -> None:
        """'cls' is not included in resolved dict for classmethods."""

        class C:
            @classmethod
            def get_initial(cls, request: HttpRequest, id: int) -> dict:  # noqa: A002, ARG003
                return {}

        r = _minimal_resolver()
        request = MagicMock(spec=HttpRequest)
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
    ) -> None:
        """*args and **kwargs are not included in resolved dict."""

        def fn(request: HttpRequest, *args: object, **kwargs: object) -> None:
            pass

        r = _minimal_resolver()
        request = MagicMock(spec=HttpRequest)
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
        """@resolver.register on a class registers an instance; next resolve uses it."""
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

        def get_user(request: HttpRequest) -> str:
            return "alice"

        resolver.register_dependency("current_user", get_user)
        try:
            request = MagicMock(spec=HttpRequest)

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

    def test_dependency_decorator_registers_callable(self) -> None:
        """@resolver.dependency('name') registers the function and returns it."""
        request = MagicMock(spec=HttpRequest)

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

    def test_depends_callable_is_called_with_di_resolved_args(self) -> None:
        """Depends(callable) resolves callable args and calls it."""

        def build_value(request: HttpRequest, id: int) -> str:  # noqa: A002
            return f"{getattr(request, 'path', '')}:{id}"

        def view(value: str = Depends(build_value)) -> str:
            return value

        r = DependencyResolver()
        request = MagicMock(spec=HttpRequest)
        request.path = "/x/"
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
        param = inspect.Parameter(
            "x",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=123,
            annotation=int,
        )
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

    def test_same_dependency_requested_twice_called_once_with_cache(self) -> None:
        """Two resolve_dependencies calls sharing _cache: dependency callable runs once."""
        call_count = 0

        def get_user(request: HttpRequest) -> str:
            nonlocal call_count
            call_count += 1
            return "alice"

        resolver.register_dependency("current_user", get_user)
        try:
            request = MagicMock(spec=HttpRequest)

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

    def test_without_cache_dependency_called_each_resolve(self) -> None:
        """Without _cache, each resolve_dependencies call invokes the dependency."""
        call_count = 0

        def get_user(request: HttpRequest) -> str:
            nonlocal call_count
            call_count += 1
            return "alice"

        resolver.register_dependency("current_user", get_user)
        try:
            request = MagicMock(spec=HttpRequest)

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
            stack = []  # name not in stack; we hit the cache[_IN_PROGRESS] branch
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
