from typing import ClassVar

from django import forms
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import Form, FormWizard


class IdentityStep(Form):
    """First wizard step capturing a name."""

    name = forms.CharField(max_length=100)


class ScopeStep(Form):
    """Second wizard step capturing a scope."""

    scope = forms.CharField(max_length=100)


class StepWizard(FormWizard):
    """Two-step page-scoped wizard whose master lives in a zone."""

    class Meta:
        """Two ordered steps routed through the step URL segment."""

        steps: ClassVar = [("identity", IdentityStep), ("scope", ScopeStep)]
        url_param = "step"

    done_payloads: ClassVar[list] = []

    def done(self, request: HttpRequest, cleaned_data: dict) -> HttpResponseRedirect:
        """Record the merged cleaned data and redirect to a thank-you page."""
        type(self).done_payloads.append(cleaned_data)
        return HttpResponseRedirect("/thanks/")
