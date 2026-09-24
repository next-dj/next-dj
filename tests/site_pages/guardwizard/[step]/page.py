from typing import ClassVar

from django import forms
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import Form, FormWizard
from next.pages import context


@context("seal_note")
def seal_note() -> str:
    """Provide the value the sealed step's zone must never hand out."""
    return "sealed-note"


class OpenStep(Form):
    """First step of the guarded wizard, served to every visitor."""

    name = forms.CharField(max_length=100)


class SealedStep(Form):
    """Second step of the guarded wizard, behind the step page's own guard."""

    seal = forms.CharField(max_length=100)


class SealedWizard(FormWizard):
    """Two-step wizard whose second step page refuses to serve itself."""

    class Meta:
        """Two ordered steps routed through the step URL segment."""

        steps: ClassVar = [("open", OpenStep), ("sealed", SealedStep)]
        url_param = "step"

    def done(self, request: HttpRequest, cleaned_data: dict) -> HttpResponseRedirect:
        """Finish the wizard with a redirect to a thank-you page."""
        return HttpResponseRedirect("/thanks/")


def render(request: HttpRequest, step: str) -> str | HttpResponseRedirect:
    """Serve a wizard step, sending a visitor away from the sealed one."""
    if step == "sealed":
        return HttpResponseRedirect("/login/")
    return "<h1>guard wizard</h1>"
