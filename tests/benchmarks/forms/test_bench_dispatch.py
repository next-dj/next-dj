"""Benchmarks for ``next.forms.dispatch`` helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from django.http import HttpResponse, HttpResponseRedirect

from next.forms.dispatch import (
    _filter_reserved_url_kwargs,
    _normalize_handler_response,
    _url_kwargs_from_post,
)


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
