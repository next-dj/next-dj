from __future__ import annotations

import datetime
import types
from decimal import Decimal
from typing import ClassVar

import pytest
from django import forms
from django.contrib.sessions.backends.cache import SessionStore
from django.http import HttpResponseRedirect
from django.test import RequestFactory

from next.forms.dispatch import FormActionDispatch
from next.forms.manager import form_action_manager
from next.forms.wizard import (
    CacheFormWizardBackend,
    FormWizard,
    SessionFormWizardBackend,
)


class _StepOne(forms.Form):
    name = forms.CharField()


class _StepTwo(forms.Form):
    email = forms.EmailField()


class _StepThree(forms.Form):
    note = forms.CharField()


class _BenchWizard(FormWizard):
    class Meta:
        steps: ClassVar = [("one", _StepOne), ("two", _StepTwo), ("three", _StepThree)]
        url_param = "step"


class BenchDispatchWizard(FormWizard):
    class Meta:
        """Three ordered steps with the default URL parameter."""

        steps: ClassVar = [("one", _StepOne), ("two", _StepTwo), ("three", _StepThree)]
        url_param = "step"

    def done(self, request, cleaned_data) -> HttpResponseRedirect:
        """Redirect once the last step validates."""
        return HttpResponseRedirect("/done/")


class _FakeSession:
    def __init__(self) -> None:
        self.session_key = "bench-session"

    def create(self) -> None:
        """Do nothing since the benchmark uses a fixed session key."""


def _wizard(step: str = "two") -> _BenchWizard:
    request = types.SimpleNamespace(path="/wizard/")
    return _BenchWizard(request=request, url_kwargs={"step": step})


class TestBenchWizardNavigation:
    """Per-request step routing the form tag drives. No storage round-trip."""

    @pytest.mark.benchmark(group="forms.wizard")
    def test_current_step(self, benchmark) -> None:
        benchmark(_wizard().current_step)

    @pytest.mark.benchmark(group="forms.wizard")
    def test_step_names(self, benchmark) -> None:
        benchmark(_wizard().step_names)

    @pytest.mark.benchmark(group="forms.wizard")
    def test_next_step(self, benchmark) -> None:
        benchmark(_wizard().next_step)

    @pytest.mark.benchmark(group="forms.wizard")
    def test_goto(self, benchmark) -> None:
        wizard = _wizard()
        benchmark(wizard.goto, "three")

    @pytest.mark.benchmark(group="forms.wizard")
    def test_current_form(self, benchmark) -> None:
        """Build an unbound step form. Sessionless storage returns no prefill."""
        benchmark(_wizard().current_form)


class TestBenchWizardDispatch:
    """End-to-end wizard step submit through `FormActionDispatch.dispatch`."""

    @pytest.mark.benchmark(group="forms.wizard.dispatch")
    def test_dispatch_wizard_step_submit(self, benchmark) -> None:
        backend = form_action_manager.default_backend
        meta = backend.get_meta("bench_dispatch_wizard")
        assert meta is not None
        request = RequestFactory().post(
            "/form/action/",
            {"_next_form_origin": "/request/one/", "name": "Ada"},
        )
        request.session = SessionStore()

        def run():
            return FormActionDispatch.dispatch(
                backend, request, "bench_dispatch_wizard", meta
            )

        response = run()
        assert response.status_code == 302
        assert response.url == "/request/two/"
        benchmark(run)


class TestBenchCacheWizardBackend:
    """Cache-backed draft persistence, one step bucket per visitor."""

    @staticmethod
    def _request() -> types.SimpleNamespace:
        return types.SimpleNamespace(session=_FakeSession())

    @pytest.mark.benchmark(group="forms.wizard")
    def test_save_step(self, benchmark) -> None:
        backend = CacheFormWizardBackend({})
        request = self._request()

        def run() -> None:
            backend.save_step(request, "wiz", "one", {"name": "x", "age": 30})

        benchmark(run)

    @pytest.mark.benchmark(group="forms.wizard")
    def test_load(self, benchmark) -> None:
        backend = CacheFormWizardBackend({})
        request = self._request()
        backend.save_step(request, "wiz", "one", {"name": "x", "age": 30})
        benchmark(backend.load, request, "wiz")


class TestBenchSessionWizardBackend:
    """Session-backed draft persistence with the typed value codec."""

    @staticmethod
    def _request() -> types.SimpleNamespace:
        return types.SimpleNamespace(session={})

    @staticmethod
    def _step_data() -> dict[str, object]:
        return {
            "name": "x",
            "age": 30,
            "day": datetime.date(2026, 6, 11),
            "amount": Decimal("9.50"),
        }

    @pytest.mark.benchmark(group="forms.wizard")
    def test_save_step(self, benchmark) -> None:
        backend = SessionFormWizardBackend({})
        request = self._request()
        data = self._step_data()

        def run() -> None:
            backend.save_step(request, "wiz", "one", data)

        benchmark(run)

    @pytest.mark.benchmark(group="forms.wizard")
    def test_load(self, benchmark) -> None:
        backend = SessionFormWizardBackend({})
        request = self._request()
        backend.save_step(request, "wiz", "one", self._step_data())
        benchmark(backend.load, request, "wiz")
