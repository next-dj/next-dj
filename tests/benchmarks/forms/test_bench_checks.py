"""Benchmarks for ``next.forms.checks``.

`manage.py check` runs both the collision check and the
`DEFAULT_FORM_ACTION_BACKENDS` shape check on every startup. These benches
pin the happy path and both error paths so a future regression to the
import-cached lookup surfaces as a measurable slowdown.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from next.forms.checks import check_form_action_backends_configuration


_VALID_TWO_ENTRY = {
    "DEFAULT_FORM_ACTION_BACKENDS": [
        {"BACKEND": "next.forms.RegistryFormActionBackend"},
        {"BACKEND": "next.forms.RegistryFormActionBackend", "OPTIONS": {}},
    ],
}

_INVALID_BACKEND_PATH = {
    "DEFAULT_FORM_ACTION_BACKENDS": [{"BACKEND": "no.such.Module"}],
}

_WRONG_SUBCLASS = {
    "DEFAULT_FORM_ACTION_BACKENDS": [{"BACKEND": "django.http.HttpResponse"}],
}


class TestBenchFormActionBackendsCheck:
    @pytest.mark.benchmark(group="forms.checks")
    def test_check_clean(self, benchmark) -> None:
        """Happy path: two valid entries that import cleanly."""
        with override_settings(NEXT_FRAMEWORK=_VALID_TWO_ENTRY):
            benchmark(check_form_action_backends_configuration)

    @pytest.mark.benchmark(group="forms.checks")
    def test_check_e044_unimportable(self, benchmark) -> None:
        """E044 path: dotted path raises ``ImportError``."""
        with override_settings(NEXT_FRAMEWORK=_INVALID_BACKEND_PATH):
            benchmark(check_form_action_backends_configuration)

    @pytest.mark.benchmark(group="forms.checks")
    def test_check_e045_wrong_subclass(self, benchmark) -> None:
        """E045 path: imported class is not a `FormActionBackend`."""
        with override_settings(NEXT_FRAMEWORK=_WRONG_SUBCLASS):
            benchmark(check_form_action_backends_configuration)
