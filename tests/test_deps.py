"""Tests for next.deps dependency resolution."""

import inspect
from unittest.mock import MagicMock

from django.http import HttpRequest

from next.deps import (
    DefaultDependencyResolver,
    FormProvider,
    HttpRequestProvider,
    RequestContext,
    UrlKwargsProvider,
    resolve_dependencies,
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

        resolver = DefaultDependencyResolver()
        request = MagicMock(spec=HttpRequest)
        request.path = "/test/"
        result = resolver.resolve_dependencies(fn, request=request)
        assert result == {"request": request}

    def test_resolve_dependencies_injects_request_and_id(self) -> None:
        """Request and url_kwargs (id) are injected."""

        def fn(request: HttpRequest, id: int) -> None:  # noqa: A002
            pass

        resolver = DefaultDependencyResolver()
        request = MagicMock(spec=HttpRequest)
        result = resolver.resolve_dependencies(fn, request=request, id=42)
        assert result == {"request": request, "id": 42}

    def test_resolve_dependencies_injects_form(self) -> None:
        """Request and form are injected when both in context."""

        def fn(request: HttpRequest, form: MagicMock) -> None:
            pass

        resolver = DefaultDependencyResolver()
        request = MagicMock(spec=HttpRequest)
        form = MagicMock()
        result = resolver.resolve_dependencies(fn, request=request, form=form)
        assert result == {"request": request, "form": form}

    def test_resolve_dependencies_empty_signature_returns_empty(self) -> None:
        """Empty dict when function has no parameters."""

        def fn() -> None:
            pass

        resolver = DefaultDependencyResolver()
        result = resolver.resolve_dependencies(fn)
        assert result == {}

    def test_resolve_dependencies_skips_self(self) -> None:
        """'self' is not included in resolved dict for bound methods."""

        class C:
            def method(self, request: HttpRequest) -> None:
                pass

        resolver = DefaultDependencyResolver()
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

        resolver = DefaultDependencyResolver()
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

        resolver = DefaultDependencyResolver()
        result = resolver.resolve_dependencies(fn)
        assert result == {"unknown": None}

    def test_resolve_dependencies_skips_var_positional_and_var_keyword(
        self,
    ) -> None:
        """*args and **kwargs are not included in resolved dict."""

        def fn(request: HttpRequest, *args: object, **kwargs: object) -> None:
            pass

        resolver = DefaultDependencyResolver()
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

        resolver = DefaultDependencyResolver(providers=[CustomProvider()])
        result = resolver.resolve_dependencies(fn)
        assert result == {"x": 100}

    def test_resolve_dependencies_invalid_signature_returns_empty(self) -> None:
        """Non-callable or invalid signature yields empty dict."""
        resolver = DefaultDependencyResolver()
        result = resolver.resolve_dependencies("not a callable")
        assert result == {}


class TestResolveDependenciesFacade:
    """Tests for resolve_dependencies facade function."""

    def test_resolve_dependencies_returns_resolved_dict(self) -> None:
        """Facade returns same result as DefaultDependencyResolver."""

        def fn(request: HttpRequest) -> None:
            pass

        request = MagicMock(spec=HttpRequest)
        result = resolve_dependencies(fn, request=request)
        assert result == {"request": request}

    def test_resolve_dependencies_with_url_kwargs(self) -> None:
        """Facade passes url_kwargs as keyword args."""

        def fn(pk: int) -> None:
            pass

        result = resolve_dependencies(fn, pk=5)
        assert result == {"pk": 5}

    def test_resolve_dependencies_with_form(self) -> None:
        """Facade passes form in context."""

        def fn(form: MagicMock) -> None:
            pass

        form = MagicMock()
        result = resolve_dependencies(fn, form=form)
        assert result == {"form": form}
