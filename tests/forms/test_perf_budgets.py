from pathlib import Path
from typing import Any, ClassVar

import pytest
from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import ActionRegistration, Form
from next.forms.manager import form_action_manager
from next.forms.wizard import (
    FormWizard,
    FormWizardBackend,
    SessionFormWizardBackend,
    wizard_backend_manager,
)
from tests.forms.actions import SimpleForm


class BudgetIdentityStep(Form):
    """First budget-wizard step."""

    name = django_forms.CharField(max_length=100)


class BudgetScopeStep(Form):
    """Second budget-wizard step."""

    scope = django_forms.CharField(max_length=100)


class BudgetExtraStep(Form):
    """Conditional budget-wizard step."""

    extra = django_forms.CharField(max_length=100)


class BudgetWizard(FormWizard):
    """Static two-step wizard for storage round-trip budgets."""

    class Meta:
        """Two ordered steps with the default URL parameter."""

        steps: ClassVar = [
            ("identity", BudgetIdentityStep),
            ("scope", BudgetScopeStep),
        ]

    def done(self, request: HttpRequest, cleaned_data: dict) -> HttpResponseRedirect:
        """Redirect once the last step validates."""
        return HttpResponseRedirect("/thanks/")


class ConditionalBudgetWizard(FormWizard):
    """Wizard whose step list consults stored data on every evaluation."""

    class Meta:
        """Two declared steps that get_steps can expand."""

        steps: ClassVar = [
            ("identity", BudgetIdentityStep),
            ("scope", BudgetScopeStep),
        ]

    def get_steps(self) -> list:
        """Append an extra step when the first answer asks for it."""
        steps = [("identity", BudgetIdentityStep), ("scope", BudgetScopeStep)]
        if self.get_all_cleaned_data().get("name") == "expand":
            steps.append(("extra", BudgetExtraStep))
        return steps

    def done(self, request: HttpRequest, cleaned_data: dict) -> HttpResponseRedirect:
        """Redirect once the last step validates."""
        return HttpResponseRedirect("/thanks/")


class _CountingWizardBackend(FormWizardBackend):
    """Counts backend round-trips while delegating to a real backend."""

    def __init__(self, inner: FormWizardBackend) -> None:
        self.inner = inner
        self.loads = 0
        self.saves = 0
        self.clears = 0

    def reset_counts(self) -> None:
        self.loads = 0
        self.saves = 0
        self.clears = 0

    def load(self, request: HttpRequest, wizard_id: str) -> dict[str, Any]:
        self.loads += 1
        return self.inner.load(request, wizard_id)

    def save_step(
        self, request: HttpRequest, wizard_id: str, step: str, data: dict[str, Any]
    ) -> None:
        self.saves += 1
        self.inner.save_step(request, wizard_id, step, data)

    def clear(self, request: HttpRequest, wizard_id: str) -> None:
        self.clears += 1
        self.inner.clear(request, wizard_id)


@pytest.fixture()
def counting_backend():
    """Install a counting wizard backend for the duration of the test."""
    counting = _CountingWizardBackend(SessionFormWizardBackend({}))
    wizard_backend_manager._backend = counting
    yield counting
    wizard_backend_manager.reset()


def _post_step(client, action: str, step: str, data: dict):
    url = form_action_manager.get_action_url(action)
    payload = {"_next_form_origin": f"/request/{step}/", **data}
    return client.post(url, data=payload, follow=False)


@pytest.mark.django_db()
class TestWizardStorageRoundTripBudgets:
    """Deterministic storage budgets for the wizard dispatch hot paths.

    A failing assertion here means a change added a storage round-trip
    to a wizard POST. Update the budget only for a feature that
    legitimately needs the extra operation.
    """

    def test_mid_step_submit_budget(self, client_no_csrf, counting_backend) -> None:
        """A valid mid-wizard step pays one save and reads nothing."""
        resp = _post_step(client_no_csrf, "budget_wizard", "identity", {"name": "Ada"})
        assert resp.status_code == 302
        assert resp.url == "/request/scope/"
        assert counting_backend.saves == 1
        assert counting_backend.loads == 0
        assert counting_backend.clears == 0

    def test_final_step_submit_budget(self, client_no_csrf, counting_backend) -> None:
        """Finalisation pays one save, one load, and one clear."""
        _post_step(client_no_csrf, "budget_wizard", "identity", {"name": "Ada"})
        counting_backend.reset_counts()
        resp = _post_step(client_no_csrf, "budget_wizard", "scope", {"scope": "ops"})
        assert resp.status_code == 302
        assert resp.url == "/thanks/"
        assert counting_backend.saves == 1
        # Exact count today: 1, the completion gate and the merged
        # cleaned data share a single load through the request memo.
        assert counting_backend.loads <= 1
        assert counting_backend.clears == 1

    def test_invalid_step_rerender_budget(
        self, client_no_csrf, counting_backend
    ) -> None:
        """An invalid step re-renders without touching wizard storage."""
        resp = _post_step(client_no_csrf, "budget_wizard", "identity", {"name": ""})
        assert resp.status_code == 200
        assert resp["X-Next-Form"] == "invalid"
        assert counting_backend.saves == 0
        assert counting_backend.loads == 0
        assert counting_backend.clears == 0

    def test_conditional_steps_submit_budget(
        self, client_no_csrf, counting_backend
    ) -> None:
        """A get_steps override that reads storage still pays a single load."""
        resp = _post_step(
            client_no_csrf,
            "conditional_budget_wizard",
            "identity",
            {"name": "expand"},
        )
        assert resp.status_code == 302
        assert resp.url == "/request/scope/"
        assert counting_backend.saves == 1
        # Exact count today: 1, the write-through after save keeps the
        # post-save get_steps re-evaluation off the backend.
        assert counting_backend.loads <= 1
        assert counting_backend.clears == 0


class TestErrorRerenderFileReadBudget:
    """File-read budget for the validation-error re-render path."""

    def test_warm_rerender_reads_no_files(
        self, mock_http_request, tmp_path, monkeypatch
    ) -> None:
        """The first re-render reads the sources, a warm one reads nothing."""
        (tmp_path / "layout.djx").write_text(
            "<html>{% block template %}{% endblock template %}</html>"
        )
        leaf = tmp_path / "leaf"
        leaf.mkdir()
        page_file = leaf / "page.py"
        page_file.write_text("")
        (leaf / "template.djx").write_text(
            "<main>{{ form.name }}{{ form.errors }}</main>"
        )
        backend = form_action_manager.default_backend
        backend.register_action(
            ActionRegistration(
                name="budget_rerender_action",
                file_path=str(page_file),
                scope="page",
                form_class=SimpleForm,
            )
        )

        reads = {"count": 0}
        original_read_text = Path.read_text

        def counting_read_text(self, *args: object, **kwargs: object) -> str:
            reads["count"] += 1
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", counting_read_text)

        request = mock_http_request(method="GET")
        form = SimpleForm(data={"name": ""})
        assert not form.is_valid()
        html = backend.render_invalid_page(
            request, "budget_rerender_action", form, page_file_path=page_file
        )
        assert "<main>" in html
        # Exact count today: 2, the template.djx body and one layout.djx.
        assert 1 <= reads["count"] <= 2

        reads["count"] = 0
        backend.render_invalid_page(
            request, "budget_rerender_action", form, page_file_path=page_file
        )
        assert reads["count"] == 0
