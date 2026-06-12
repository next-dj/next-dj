from typing import Any, ClassVar

from access.models import AccessRequest
from django import forms
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import ComponentWidget, FormWizard


class IdentityStep(forms.ModelForm):
    """First wizard step capturing who is asking for access."""

    class Meta:
        model = AccessRequest
        fields: ClassVar = ["full_name", "email", "team"]
        widgets: ClassVar = {
            "full_name": ComponentWidget("input"),
            "email": ComponentWidget("input", type="email"),
            "team": ComponentWidget("input"),
        }


class ScopeStep(forms.ModelForm):
    """Second wizard step capturing what access is requested and for how long."""

    class Meta:
        model = AccessRequest
        fields: ClassVar = ["project_slug", "reason", "expires_in_days"]
        widgets: ClassVar = {
            "project_slug": ComponentWidget("input"),
            "reason": ComponentWidget("textarea", rows=4),
            "expires_in_days": ComponentWidget("input", type="number"),
        }


class ApprovalStep(forms.Form):
    """Final wizard step that only confirms the merged request."""


class AccessRequestWizard(FormWizard):
    """Three-step access-request wizard with session-backed drafts."""

    class Meta:
        steps: ClassVar = [
            ("identity", IdentityStep),
            ("scope", ScopeStep),
            ("approval", ApprovalStep),
        ]
        url_param = "step"

    def done(
        self,
        request: HttpRequest,
        cleaned_data: dict[str, Any],
    ) -> HttpResponseRedirect:
        """Create the request from the merged steps and link the next dispatch."""
        access_request = AccessRequest.objects.create(**cleaned_data)
        request.session["access_request_just_created"] = access_request.pk
        request.session.modified = True
        return HttpResponseRedirect(f"/request/{access_request.pk}/audit/?just=1")
