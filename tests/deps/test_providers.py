import importlib.util
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest

from next.deps import resolver
from next.deps.providers import ProviderRegistry
from next.forms import DForm, FormProvider
from next.pages.context import ContextByNameProvider
from next.urls import (
    DUrl,
    HttpRequestProvider,
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
    """Tests for ``_coerce_url_value``."""

    @pytest.mark.parametrize(
        "case", COERCE_URL_VALUE_CASES, ids=[c.id for c in COERCE_URL_VALUE_CASES]
    )
    def test_coerce(self, case: CoerceUrlValueCase) -> None:
        """Coercion follows int, bool, float, and str rules."""
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
