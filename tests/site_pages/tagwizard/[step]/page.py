from typing import ClassVar

from django import forms
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import Form, FormWizard


class TaggedIdentityStep(Form):
    """First step of the tag-rendered wizard."""

    full_name = forms.CharField(max_length=50)
    email = forms.EmailField()


class TaggedTeamStep(Form):
    """Second step of the tag-rendered wizard."""

    team = forms.CharField(max_length=50)


class TaggedWizard(FormWizard):
    """Two-step wizard whose zone body renders the step through the form tag."""

    class Meta:
        """Two ordered steps routed through the step URL segment."""

        steps: ClassVar = [("identity", TaggedIdentityStep), ("team", TaggedTeamStep)]
        url_param = "step"

    def done(self, request: HttpRequest, cleaned_data: dict) -> HttpResponseRedirect:
        """Redirect to a thank-you page once both steps are stored."""
        del request, cleaned_data
        return HttpResponseRedirect("/thanks/")
