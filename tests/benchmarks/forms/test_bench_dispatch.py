from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from django import forms
from django.http import HttpResponse, HttpResponseRedirect

from next.forms import Form
from next.forms.backends import ActionRegistration, RegistryFormActionBackend
from next.forms.dispatch import (
    FormActionDispatch,
    _normalize_handler_response,
)
from next.forms.origin import (
    _ORIGIN_MATCH_ATTR,
    _filter_reserved_url_kwargs,
    resolve_origin,
)
from tests.support.helpers import build_mock_http_request


class _RedirectDuck:
    """Plain `.url` carrier so the bench measures the duck path, not MagicMock."""

    url = "/redirect-target/"


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
        raw = _RedirectDuck()
        benchmark(_normalize_handler_response, raw)
        assert isinstance(_normalize_handler_response(raw), HttpResponseRedirect)

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_filter_reserved_url_kwargs(self, benchmark) -> None:
        payload = {f"k_{i}": i for i in range(30)}
        payload.update({"request": 1, "form": 2})
        benchmark(_filter_reserved_url_kwargs, payload)

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_resolve_origin_cold(self, benchmark) -> None:
        request = build_mock_http_request(
            method="POST", POST={"_next_form_origin": "/items/42/"}
        )

        def run() -> object:
            if hasattr(request, _ORIGIN_MATCH_ATTR):
                delattr(request, _ORIGIN_MATCH_ATTR)
            return resolve_origin(request)

        match = benchmark(run)
        assert match is not None
        assert match.url_kwargs == {"id": 42}

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_resolve_origin_memoised(self, benchmark) -> None:
        request = build_mock_http_request(
            method="POST", POST={"_next_form_origin": "/items/42/"}
        )
        first = resolve_origin(request)
        match = benchmark(resolve_origin, request)
        assert match is first


class _BenchForm(Form):
    """Minimal form used by the end-to-end dispatch benches."""

    name = forms.CharField(max_length=32)


def _ok_handler(**_kwargs: object) -> HttpResponseRedirect:
    return HttpResponseRedirect("/")


class TestBenchDispatchEndToEnd:
    @pytest.mark.benchmark(group="forms.dispatch")
    def test_dispatch_valid_form(self, benchmark) -> None:
        """Valid submission — handler runs, redirect emitted."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="bench_action",
                file_path=__file__,
                scope="shared",
                handler=_ok_handler,
                form_class=_BenchForm,
            )
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
            ActionRegistration(
                name="bench_action",
                file_path=__file__,
                scope="shared",
                handler=_ok_handler,
                form_class=_BenchForm,
            )
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

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_dispatch_unguarded_form_no_hook_overhead(self, benchmark) -> None:
        """A no-hook form pays no permission-hook resolve on the dispatch path.

        Pins the zero-overhead promise that an undeclared check_permissions or
        has_object_permission costs the dispatcher only the two ClassVar reads,
        not a third resolver call.
        """
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="bench_action",
                file_path=__file__,
                scope="shared",
                handler=_ok_handler,
                form_class=_BenchForm,
            )
        )
        meta = backend.get_meta("bench_action")
        assert meta is not None
        assert _BenchForm._has_check_permissions is False
        assert _BenchForm._has_object_permission is False
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
    def test_dispatch_through_subclassed_backend(self, benchmark) -> None:
        """Dispatch through a thin `RegistryFormActionBackend` subclass.

        Pins the wrapper overhead for projects that inherit from the
        registry backend (audit-trail, metrics, gating). Compare against
        ``test_dispatch_valid_form`` to spot regressions in the
        super-call path.
        """

        class _SubclassedBackend(RegistryFormActionBackend):
            def dispatch(self, request, uid):  # type: ignore[override]
                return super().dispatch(request, uid)

        backend = _SubclassedBackend()
        backend.register_action(
            ActionRegistration(
                name="bench_action",
                file_path=__file__,
                scope="shared",
                handler=_ok_handler,
                form_class=_BenchForm,
            )
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
