from typing import Any, ClassVar

from access.models import AccessRequest
from access.policy import POLICY_FIELD, AcknowledgedStep
from django import forms as django_forms
from django.http import HttpRequest, HttpResponse

from next.forms import ComponentWidget, FormWizard, PermissionOutcome
from next.partial import Patches, PatchResponse, partial_intent


class IdentityStep(AcknowledgedStep, django_forms.ModelForm):
    """First wizard step capturing who is asking for access."""

    class Meta:
        model = AccessRequest
        fields: ClassVar = ["full_name", "email", "team"]
        widgets: ClassVar = {
            "full_name": ComponentWidget("input"),
            "email": ComponentWidget("input", type="email"),
            "team": ComponentWidget("input"),
        }


class ScopeStep(AcknowledgedStep, django_forms.ModelForm):
    """Second wizard step capturing what access is requested and for how long."""

    class Meta:
        model = AccessRequest
        fields: ClassVar = ["project_slug", "reason", "expires_in_days"]
        widgets: ClassVar = {
            "project_slug": ComponentWidget("input"),
            "reason": ComponentWidget("textarea", rows=4),
            "expires_in_days": ComponentWidget("input", type="number"),
        }


class ApprovalStep(AcknowledgedStep):
    """Final wizard step that only confirms the merged request."""


class AccessRequestWizard(FormWizard):
    """Three-step access-request wizard with cache-backed step drafts."""

    class Meta:
        steps: ClassVar = [
            ("identity", IdentityStep),
            ("scope", ScopeStep),
            ("approval", ApprovalStep),
        ]
        url_param = "step"

    @classmethod
    def check_permissions(cls, request: HttpRequest) -> PermissionOutcome:
        """Deny every binding step POST that omits the retention acknowledgement.

        A replayed action URL that never rendered the form is denied before any PII
        binds, while a blur-validation probe checking one field is let through unacked.
        """
        if partial_intent(request).validate_fields:
            return True
        return AcknowledgedStep.is_acknowledged(request)

    def done(
        self, request: HttpRequest, cleaned_data: dict[str, Any]
    ) -> PatchResponse | HttpResponse:
        """Create the request, close the wizard layer, and link the next dispatch.

        The acknowledgement rides every step as a control field, so it is dropped here
        before the fields reach the model instead of being declared on it.
        """
        fields = {k: v for k, v in cleaned_data.items() if k != POLICY_FIELD}
        access_request = AccessRequest.objects.create(**fields)
        request.session["access_request_just_created"] = access_request.pk
        request.session.modified = True
        return (
            Patches(request)
            .layer_close(result={"id": access_request.pk})
            .toast("Access request submitted", variant="success")
            .response(fallback=f"/request/{access_request.pk}/audit/?just=1")
        )
