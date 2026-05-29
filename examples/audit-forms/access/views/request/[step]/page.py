from typing import Any, ClassVar

from access.models import AccessRequest
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import (
    EmailInput,
    Form,
    FormWizard,
    ModelForm,
    NumberInput,
    Textarea,
    TextInput,
)


_INPUT_CLASS = (
    "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm "
    "text-slate-900 placeholder:text-slate-400 focus:outline-none "
    "focus:ring-2 focus:ring-slate-400 focus:border-transparent"
)
_INPUT_ATTRS = {"class": _INPUT_CLASS}
_TEXTAREA_ATTRS = {"class": _INPUT_CLASS + " resize-none", "rows": "4"}


class IdentityStep(ModelForm):
    """First wizard step capturing who is asking for access."""

    class Meta:
        model = AccessRequest
        fields: ClassVar = ["full_name", "email", "team"]
        widgets: ClassVar = {
            "full_name": TextInput(attrs=_INPUT_ATTRS),
            "email": EmailInput(attrs=_INPUT_ATTRS),
            "team": TextInput(attrs=_INPUT_ATTRS),
        }


class ScopeStep(ModelForm):
    """Second wizard step capturing what access is requested and for how long."""

    class Meta:
        model = AccessRequest
        fields: ClassVar = ["project_slug", "reason", "expires_in_days"]
        widgets: ClassVar = {
            "project_slug": TextInput(attrs=_INPUT_ATTRS),
            "reason": Textarea(attrs=_TEXTAREA_ATTRS),
            "expires_in_days": NumberInput(attrs=_INPUT_ATTRS),
        }


class ApprovalStep(Form):
    """Final wizard step that only confirms the merged request."""


class AccessRequestWizard(FormWizard):
    """Three-step access-request wizard backed by the configured cache."""

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
