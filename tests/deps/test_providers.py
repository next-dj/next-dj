import importlib.util
import inspect
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest

from next.deps import DependencyResolver, resolver
from next.deps.markers import DependsProvider
from next.forms import DForm
from next.forms.markers import FormProvider
from next.pages.context import ContextByDefaultProvider, ContextByNameProvider
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
    CoerceUrlValueCase,
    UrlByAnnotationResolveCase,
    UrlKwargsResolveCase,
    _ctx,
    build_mock_http_request,
    inspect_parameter,
)


def _mock_request_factory() -> MagicMock:
    return build_mock_http_request()


def _no_request() -> None:
    return None


class TestResolverDescriptor:
    """``ContextByNameProvider`` accesses ``resolver`` via class descriptor."""

    def test_context_provider_resolver_attribute_returns_singleton(self) -> None:
        """ContextByNameProvider has no resolver in ``__init__``. ``self.resolver`` returns the global resolver."""
        provider = ContextByNameProvider()
        assert provider.resolver is resolver


class TestHttpRequestProvider:
    """Tests for HttpRequestProvider."""

    @pytest.mark.parametrize(
        ("request_obj", "annotation", "expected"),
        [
            (_mock_request_factory, HttpRequest, True),
            (_mock_request_factory, HttpRequest | None, True),
            (_mock_request_factory, HttpRequest | int, False),
            (_mock_request_factory, int | None, False),
            (_no_request, HttpRequest, False),
            (_no_request, HttpRequest | None, False),
            (_mock_request_factory, inspect.Parameter.empty, False),
        ],
        ids=[
            "request_present",
            "pep604_optional",
            "union_with_other_type",
            "int_or_none",
            "request_none",
            "request_none_optional_annotation",
            "annotation_empty",
        ],
    )
    def test_can_handle(self, request_obj, annotation, expected) -> None:
        """can_handle matches request presence and HttpRequest-or-None annotation."""
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

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                "from django.http import HttpRequest\n"
                "def handler(request: HttpRequest):\n"
                "    pass\n",
                True,
            ),
            (
                "from django.http import HttpRequest\n"
                "def handler(request: HttpRequest | None = None):\n"
                "    pass\n",
                True,
            ),
            (
                "from django.http import HttpRequest\n"
                "def handler(request: HttpRequest | int = 0):\n"
                "    pass\n",
                False,
            ),
        ],
        ids=[
            "type_hints_bare_request",
            "type_hints_pep604_optional",
            "type_hints_union_other_type",
        ],
    )
    def test_can_handle_via_get_type_hints_branch(
        self, tmp_path: Path, source, expected
    ) -> None:
        """The `get_type_hints` branch accepts the same forms as the fallback."""
        mod_path = tmp_path / "stack_ann.py"
        mod_path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("stack_ann", mod_path)
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        handler = mod.handler

        provider = HttpRequestProvider()
        param = inspect.signature(handler).parameters["request"]
        ctx = _ctx(request=HttpRequest())
        resolver._resolve_call_stack.append(handler)
        try:
            assert provider.can_handle(param, ctx) is expected
        finally:
            resolver._resolve_call_stack.pop()

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
    """Tests for UrlKwargsProvider."""

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
    """Table-driven checks for ``_coerce_url_value``."""

    @pytest.mark.parametrize(
        "case", COERCE_URL_VALUE_CASES, ids=[c.id for c in COERCE_URL_VALUE_CASES]
    )
    def test_coerce(self, case: CoerceUrlValueCase) -> None:
        """Apply registered coercions and retain the input when conversion cannot run."""
        assert _coerce_url_value(case.raw, case.hint) == case.expected


class TestUrlByAnnotationProvider:
    """Tests for UrlByAnnotationProvider."""

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
    """Tests for FormProvider."""

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


class TestProviderPriority:
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
        instance._ensure_providers()
        names = [type(p).__name__ for p in instance._providers]
        builtins = [name for name in names if name in self.EXPECTED_ORDER]
        assert builtins == self.EXPECTED_ORDER
