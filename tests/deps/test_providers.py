import inspect
from operator import attrgetter
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from django.core.handlers.asgi import ASGIRequest
from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpRequest

from next.deps import DependencyResolver, ParameterProvider, resolver
from next.deps.markers import DependsProvider
from next.deps.providers import CompilingParameterProvider
from next.forms import DForm
from next.forms.markers import FormProvider
from next.pages.context import Context, ContextByDefaultProvider, ContextByNameProvider
from next.testing import make_resolution_context
from next.urls import (
    DUrl,
    HttpRequestProvider,
    QueryParamProvider,
    UrlByAnnotationProvider,
    UrlKwargsProvider,
)
from next.urls.parser import _coerce_url_value
from tests.support import (
    COERCE_URL_VALUE_CASES,
    URL_BY_ANNOTATION_RESOLVE_CASES,
    URL_KWARGS_RESOLVE_CASES,
    AForm,
    CoerceUrlValueCase,
    ContextMarkerCase,
    DeferringProvider,
    OtherForm,
    UrlByAnnotationResolveCase,
    UrlKwargsResolveCase,
    _ctx,
    build_mock_http_request,
    inspect_parameter,
    typing_optional,
)


def _mock_request_factory() -> MagicMock:
    return build_mock_http_request()


def _mock_wsgi_request_factory() -> MagicMock:
    return MagicMock(spec=WSGIRequest)


def _stand_in_request_factory() -> MagicMock:
    return MagicMock()


def _no_request() -> None:
    return None


class TestProviderResolverAttribute:
    """``ContextByNameProvider`` reads ``resolver`` from the class attribute."""

    def test_context_provider_resolver_attribute_returns_singleton(self) -> None:
        """A provider without a ``resolver`` argument sees the global resolver."""
        provider = ContextByNameProvider()
        assert provider.resolver is resolver
        assert ContextByNameProvider.resolver is resolver


class TestHttpRequestProvider:
    """Tests for HttpRequestProvider."""

    @pytest.mark.parametrize(
        ("request_obj", "annotation", "expected"),
        [
            (_mock_request_factory, HttpRequest, True),
            (_mock_request_factory, HttpRequest | None, True),
            (_mock_request_factory, typing_optional(HttpRequest), True),
            (_stand_in_request_factory, HttpRequest, True),
            (_stand_in_request_factory, HttpRequest | None, True),
            (_mock_wsgi_request_factory, WSGIRequest, True),
            (_mock_wsgi_request_factory, typing_optional(WSGIRequest), True),
            (_mock_request_factory, WSGIRequest, False),
            (_mock_wsgi_request_factory, ASGIRequest | None, False),
            (_mock_request_factory, HttpRequest | int, False),
            (_mock_request_factory, int | None, False),
            (_mock_request_factory, "HttpRequest", False),
            (_mock_request_factory, list[HttpRequest], False),
            (_no_request, HttpRequest, False),
            (_no_request, HttpRequest | None, False),
            (_mock_request_factory, inspect.Parameter.empty, False),
        ],
        ids=[
            "request_present",
            "pep604_optional",
            "typing_optional",
            "stand_in_under_the_base_annotation",
            "stand_in_under_the_optional_base_annotation",
            "wsgi_subclass",
            "wsgi_subclass_typing_optional",
            "base_request_under_a_subclass_annotation",
            "wsgi_request_under_an_asgi_annotation",
            "union_with_other_type",
            "int_or_none",
            "string_annotation",
            "generic_alias",
            "request_none",
            "request_none_optional_annotation",
            "annotation_empty",
        ],
    )
    def test_can_handle(self, request_obj, annotation, expected) -> None:
        """can_handle needs the request in context to inhabit the annotated class."""
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

    def test_string_annotation_resolves_through_the_plan(
        self, mock_http_request
    ) -> None:
        """The plan carries the resolved hint, so `can_handle` never reads the callable."""

        def handler(request: "HttpRequest | None" = None) -> None:
            return None

        request = mock_http_request()
        instance = DependencyResolver()
        assert instance.resolve_dependencies(handler, request=request) == {
            "request": request
        }

    def test_unresolvable_string_annotation_falls_back(self) -> None:
        """A hint the module cannot resolve leaves the raw string, which claims nothing."""

        def handler(request: "inspect.DoesNotExist | None" = None) -> None:
            return None

        instance = DependencyResolver()
        assert instance.resolve_dependencies(handler, request=HttpRequest()) == {
            "request": None
        }


class TestUrlKwargsProvider:
    """Tests for UrlKwargsProvider."""

    @pytest.mark.parametrize(
        ("url_kwargs", "expected"),
        [({"id": 42}, True), ({}, False)],
        ids=["name_in_kwargs", "name_missing"],
    )
    def test_can_handle(self, url_kwargs, expected) -> None:
        """can_handle is True exactly when param name appears in url_kwargs."""
        provider = UrlKwargsProvider()
        param = inspect_parameter("id", int)
        ctx = _ctx(url_kwargs=url_kwargs)
        assert provider.can_handle(param, ctx) is expected

    @pytest.mark.parametrize("case", URL_KWARGS_RESOLVE_CASES, ids=lambda case: case.id)
    def test_resolve(self, case: UrlKwargsResolveCase) -> None:
        """Resolve applies annotation coercion and missing key rules."""
        provider = UrlKwargsProvider()
        param = inspect_parameter(case.name, case.annotation)
        ctx = _ctx(url_kwargs=case.url_kwargs)
        assert provider.resolve(param, ctx) == case.expected


class TestCoerceUrlValue:
    """Table-driven checks for ``_coerce_url_value``."""

    @pytest.mark.parametrize("case", COERCE_URL_VALUE_CASES, ids=lambda case: case.id)
    def test_coerce(self, case: CoerceUrlValueCase) -> None:
        """Apply registered coercions and retain the input when conversion cannot run."""
        assert _coerce_url_value(case.raw, case.hint) == case.expected


class TestUrlByAnnotationProvider:
    """Tests for UrlByAnnotationProvider."""

    @pytest.mark.parametrize(
        ("annotation", "expected"),
        [(DUrl[int], True), (int, False)],
        ids=["durl", "plain_int"],
    )
    def test_can_handle(self, annotation, expected) -> None:
        """can_handle is True only for DUrl annotations."""
        provider = UrlByAnnotationProvider()
        param = inspect_parameter("id", annotation)
        ctx = _ctx()
        assert provider.can_handle(param, ctx) is expected

    @pytest.mark.parametrize(
        "case", URL_BY_ANNOTATION_RESOLVE_CASES, ids=lambda case: case.id
    )
    def test_resolve(self, case: UrlByAnnotationResolveCase) -> None:
        """Resolve reads url_kwargs by param name and coerces via DUrl."""
        provider = UrlByAnnotationProvider()
        param = inspect_parameter(case.name, case.annotation)
        ctx = _ctx(url_kwargs=case.url_kwargs)
        assert provider.resolve(param, ctx) == case.expected

    @pytest.mark.parametrize(
        "case", URL_BY_ANNOTATION_RESOLVE_CASES, ids=lambda case: case.id
    )
    def test_compile_resolve_matches_resolve(
        self, case: UrlByAnnotationResolveCase
    ) -> None:
        """The compiled filler answers what the plain resolve answers."""
        provider = UrlByAnnotationProvider()
        param = inspect_parameter(case.name, case.annotation)
        ctx = _ctx(url_kwargs=case.url_kwargs)
        fill = provider.compile_resolve(param)
        assert fill is not None
        assert fill(ctx) == case.expected
        assert fill(ctx) == provider.resolve(param, ctx)

    def test_named_key_survives_the_resolved_hint(self) -> None:
        """The segment name reaches the provider through a compiled plan."""

        def fetch_note(note_id: DUrl["id", int]) -> object:
            return note_id

        assert resolver.resolve_dependencies(fetch_note, id="42") == {"note_id": 42}


class TestFormProvider:
    """Tests for FormProvider."""

    @pytest.mark.parametrize(
        ("param_name", "form_kind", "expected"),
        [("form", "mock", True), ("form", "none", False), ("other", "mock", False)],
        ids=["name_form_with_instance", "form_none", "wrong_name"],
    )
    def test_can_handle_basic(self, param_name, form_kind, expected) -> None:
        """can_handle for name 'form', missing form, and non-form param names."""
        provider = FormProvider()
        param = inspect_parameter(param_name, inspect.Parameter.empty)
        form = None if form_kind == "none" else AForm()
        ctx = _ctx(form=form)
        assert provider.can_handle(param, ctx) is expected

    def test_can_handle_true_when_annotation_is_form_class_and_instance_matches(
        self,
    ) -> None:
        """can_handle is True when param annotation matches form instance type."""
        provider = FormProvider()
        form = AForm()
        param = inspect_parameter("f", AForm)
        ctx = _ctx(form=form)
        assert provider.can_handle(param, ctx) is True

    def test_can_handle_false_when_annotation_not_matching_instance(self) -> None:
        """can_handle is False when form instance type does not match annotation."""
        provider = FormProvider()
        param = inspect_parameter("f", OtherForm)
        ctx = _ctx(form=AForm())
        assert provider.can_handle(param, ctx) is False

    def test_a_class_that_is_no_django_form_is_refused(self) -> None:
        """An annotation outside `BaseForm` never claims the form in context."""

        class NotAForm:
            pass

        provider = FormProvider()
        param = inspect_parameter("f", NotAForm)
        assert provider.static_can_handle(param) is False
        assert provider.can_handle(param, _ctx(form=NotAForm())) is False

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
        form = AForm()
        param = inspect_parameter("form", inspect.Parameter.empty)
        ctx = _ctx(form=form)
        assert provider.resolve(param, ctx) is form


class TestBuiltinPriorityOrder:
    """Built-in providers are consulted in an explicit, pinned priority order."""

    EXPECTED_ORDER: ClassVar[list[str]] = [
        "DependsProvider",
        "ContextByDefaultProvider",
        "ContextByNameProvider",
        "FormProvider",
        "HttpRequestProvider",
        "UrlByAnnotationProvider",
        "UrlKwargsProvider",
        "QueryParamProvider",
    ]

    def test_builtin_priorities_are_pinned(self) -> None:
        """Each built-in provider declares its documented priority value."""
        assert DependsProvider.priority == 10
        assert ContextByDefaultProvider.priority == 20
        assert ContextByNameProvider.priority == 30
        assert FormProvider.priority == 40
        assert HttpRequestProvider.priority == 50
        assert UrlByAnnotationProvider.priority == 60
        assert UrlKwargsProvider.priority == 70
        assert QueryParamProvider.priority == 80

    def test_resolver_consults_builtins_in_priority_order(self) -> None:
        """The lazy auto-registry yields the built-in providers in priority order."""
        instance = DependencyResolver()
        instance._sync_providers()
        names = [type(p).__name__ for p in instance._providers]
        builtins = [name for name in names if name in self.EXPECTED_ORDER]
        assert builtins == self.EXPECTED_ORDER


class TestReservedContextKeys:
    """The context providers stay blind to the names dedicated providers own."""

    def test_the_name_provider_rules_a_reserved_name_out_for_good(self) -> None:
        provider = ContextByNameProvider()
        reserved = inspect_parameter("request")
        assert provider.static_can_handle(reserved) is False
        assert (
            provider.can_handle(reserved, _ctx(context_data={"request": "x"})) is False
        )
        free = inspect_parameter("other")
        assert provider.static_can_handle(free) is None
        assert provider.can_handle(free, _ctx(context_data={"other": "x"})) is True

    @pytest.mark.parametrize("source", [None, "request"])
    def test_the_default_marker_reads_no_reserved_key(self, source) -> None:
        provider = ContextByDefaultProvider(resolver)
        marker = Context(source, default="fallback")
        param = inspect_parameter("request", default=marker)
        ctx = _ctx(context_data={"request": "from-context"})
        assert provider.resolve(param, ctx) == "fallback"

    @pytest.mark.parametrize(
        "source", [None, "request"], ids=["from_param_name", "from_named_key"]
    )
    def test_the_compiled_default_marker_reads_no_reserved_key(self, source) -> None:
        provider = ContextByDefaultProvider(resolver)
        marker = Context(source, default="fallback")
        param = inspect_parameter("request", default=marker)
        fill = provider.compile_resolve(param)
        assert fill is not None
        assert fill(_ctx(context_data={"request": "from-context"})) == "fallback"


class TestProviderProtocols:
    """The optional compile hook sits in its own structural contract."""

    def test_a_provider_without_the_hook_is_still_a_parameter_provider(self) -> None:
        provider = DeferringProvider(None)
        assert isinstance(provider, ParameterProvider)
        assert not isinstance(provider, CompilingParameterProvider)

    def test_a_marker_provider_satisfies_both_protocols(self) -> None:
        provider = UrlByAnnotationProvider()
        assert isinstance(provider, ParameterProvider)
        assert isinstance(provider, CompilingParameterProvider)


def _made(page_value: int = Context("page_value")) -> str:
    return f"made-{page_value}"


CONTEXT_MARKER_CASES: tuple[ContextMarkerCase, ...] = (
    ContextMarkerCase("from_param_name", None, {"value": 7}, 7),
    ContextMarkerCase("from_named_key", "page_value", {"page_value": 7}, 7),
    ContextMarkerCase("missing_key", None, {}, "fallback"),
    ContextMarkerCase("missing_named_key", "page_value", {}, "fallback"),
    ContextMarkerCase("callable_source", _made, {"page_value": 7}, "made-7"),
    ContextMarkerCase("constant_source", 42, {}, 42),
)


class TestContextMarkerForms:
    """Both paths of the `Context` marker answer the same for every source."""

    @pytest.mark.parametrize("case", CONTEXT_MARKER_CASES, ids=attrgetter("id"))
    def test_resolve_and_compiled_filler_agree(self, case: ContextMarkerCase) -> None:
        provider = ContextByDefaultProvider(DependencyResolver())
        marker = Context(case.source, default="fallback")
        param = inspect_parameter("value", default=marker)
        fill = provider.compile_resolve(param)
        assert fill is not None
        context = make_resolution_context(context_data=case.context_data)
        assert provider.resolve(param, context) == case.expected
        assert fill(context) == case.expected

    def test_resolve_ignores_a_default_that_is_no_marker(self) -> None:
        provider = ContextByDefaultProvider(DependencyResolver())
        param = inspect_parameter("value", default="plain")
        assert provider.resolve(param, make_resolution_context()) is None

    def test_an_unset_default_reads_as_none(self) -> None:
        provider = ContextByDefaultProvider(DependencyResolver())
        param = inspect_parameter("value", default=Context())
        fill = provider.compile_resolve(param)
        assert fill is not None
        assert fill(make_resolution_context()) is None
        assert provider.resolve(param, make_resolution_context()) is None
