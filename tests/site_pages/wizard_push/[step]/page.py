from typing import ClassVar

from django import forms
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import Form, FormWizard


class PushIdentityStep(Form):
    """First step of the push wizard capturing a name."""

    name = forms.CharField(max_length=100)


class PushScopeStep(Form):
    """Second step of the push wizard capturing a scope."""

    scope = forms.CharField(max_length=100)


class PushStepWizard(FormWizard):
    """Wizard that opts into pushing its steps to browser history."""

    class Meta:
        """Two ordered steps with step history pushing enabled."""

        steps: ClassVar = [
            ("identity", PushIdentityStep),
            ("scope", PushScopeStep),
        ]
        url_param = "step"
        push_steps = True

    done_payloads: ClassVar[list] = []

    def done(self, request: HttpRequest, cleaned_data: dict) -> HttpResponseRedirect:
        """Record the merged cleaned data and redirect."""
        type(self).done_payloads.append(cleaned_data)
        return HttpResponseRedirect("/thanks/")
