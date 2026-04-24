"""Benchmarks for ``next.forms.dispatch`` helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from django import forms
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.forms import Form
from next.forms.backends import FormActionOptions, RegistryFormActionBackend
from next.forms.dispatch import (
    FormActionDispatch,
    _filter_reserved_url_kwargs,
    _normalize_handler_response,
    _url_kwargs_from_post,
)
from tests.support.helpers import build_mock_http_request


class TestBenchDispatchHelpers:
    @pytest.mark.benchmark(group="forms.dispatch")
    def test_normalize_none(self, benchmark) -> None:
        benchmark(_normalize_handler_response, None)

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_normalize_httpresponse(self, benchmark) -> None:
        response = HttpResponse("ok")
        benchmark(_normalize_handler_response, response)

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_normalize_str(self, benchmark) -> None:
        benchmark(_normalize_handler_response, "hello world")

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_normalize_redirect_duck(self, benchmark) -> None:
        raw = MagicMock(url="/redirect-target/")
        benchmark(_normalize_handler_response, raw)
        assert isinstance(_normalize_handler_response(raw), HttpResponseRedirect)

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_filter_reserved_url_kwargs(self, benchmark) -> None:
        payload = {f"k_{i}": i for i in range(30)}
        payload.update({"request": 1, "form": 2})
        benchmark(_filter_reserved_url_kwargs, payload)

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_url_kwargs_from_post(self, benchmark) -> None:
        request = MagicMock()
        request.POST = {
            **{f"_url_param_k_{i}": str(i) for i in range(20)},
            "csrf": "x",
        }
        benchmark(_url_kwargs_from_post, request)


class _BenchForm(Form):
    """Minimal form used by the end-to-end dispatch benches."""

    name = forms.CharField(max_length=32)


def _ok_handler(
    _request: HttpRequest,
    _form: _BenchForm,
    **_kwargs: object,
) -> HttpResponseRedirect:
    return HttpResponseRedirect("/")


class TestBenchDispatchEndToEnd:
    @pytest.mark.benchmark(group="forms.dispatch")
    def test_dispatch_valid_form(self, benchmark) -> None:
        """Valid submission — handler runs, redirect emitted."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            "bench_action",
            _ok_handler,
            options=FormActionOptions(form_class=_BenchForm),
        )
        meta = backend.get_meta("bench_action")
        assert meta is not None
        post = MagicMock()
        post.items.return_value = [("name", "bench")]
        request = build_mock_http_request(method="POST", POST=post, FILES=None)
        benchmark(
            FormActionDispatch.dispatch,
            backend,
            request,
            "bench_action",
            meta,
        )

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_dispatch_invalid_form(self, benchmark) -> None:
        """Invalid submission — validation_failed signal + errors payload."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            "bench_action",
            _ok_handler,
            options=FormActionOptions(form_class=_BenchForm),
        )
        meta = backend.get_meta("bench_action")
        assert meta is not None
        # name is required but missing → ValidationError → error path.
        post = MagicMock()
        post.items.return_value = []
        request = build_mock_http_request(method="POST", POST=post, FILES=None)
        benchmark(
            FormActionDispatch.dispatch,
            backend,
            request,
            "bench_action",
            meta,
        )
