from __future__ import annotations

import types

import pytest
from django import forms

from next.forms import Form
from next.forms.backends import ActionRegistration
from next.forms.manager import build_form_namespace_for_action, form_action_manager


class _NamespaceForm(Form):
    title = forms.CharField()
    body = forms.CharField(widget=forms.Textarea)


_ACTION_NAME = "bench_namespace_action"


@pytest.fixture(autouse=True)
def _register_namespace_action():
    form_action_manager._ensure_backends()
    form_action_manager.register_action(
        ActionRegistration(
            name=_ACTION_NAME,
            file_path=__file__,
            scope="page",
            form_class=_NamespaceForm,
        )
    )


class TestBenchBuildFormNamespace:
    """`{% form %}` resolves an action name to a form namespace on every render."""

    @pytest.mark.benchmark(group="forms.namespace")
    def test_build_form_namespace_form_class(self, benchmark) -> None:
        request = types.SimpleNamespace(method="GET")
        benchmark(build_form_namespace_for_action, _ACTION_NAME, request, __file__)
