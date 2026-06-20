from typing import Any, ClassVar

from access.models import AccessRequest
from django import forms as django_forms
from django.http import HttpRequest, HttpResponse

from next.forms import ComponentWidget, FormWizard, PermissionOutcome
from next.partial import Patches, PatchResponse, partial_intent


class IdentityStep(django_forms.ModelForm):
    """First wizard step capturing who is asking for access."""

    class Meta:
        model = AccessRequest
        fields: ClassVar = ["full_name", "email", "team"]
        widgets: ClassVar = {
            "full_name": ComponentWidget("input"),
            "email": ComponentWidget("input", type="email"),
            "team": ComponentWidget("input"),
        }


class ScopeStep(django_forms.ModelForm):
    """Second wizard step capturing what access is requested and for how long."""

    class Meta:
        model = AccessRequest
        fields: ClassVar = ["project_slug", "reason", "expires_in_days"]
        widgets: ClassVar = {
            "project_slug": ComponentWidget("input"),
            "reason": ComponentWidget("textarea", rows=4),
            "expires_in_days": ComponentWidget("input", type="number"),
        }


class ApprovalStep(django_forms.Form):
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

        The form page renders the acknowledgement notice and carries the
        `policy_acknowledged` field, so a normal submission passes while a
        replayed or forged action URL that never rendered the form is
        denied before any PII binds. A denied step writes no draft and
        leaves only the `form_access_denied` audit row behind.

        A blur-validation probe binds no data and asks only whether one
        field is well formed, so it is let through ahead of the
        acknowledgement the user has not reached yet.
        """
        if partial_intent(request).validate_fields:
            return True
        return request.POST.get("policy_acknowledged") == "on"

    def done(
        self,
        request: HttpRequest,
        cleaned_data: dict[str, Any],
    ) -> PatchResponse | HttpResponse:
        """Create the request, close the wizard layer, and link the next dispatch.

        With a live runtime the final step closes the modal with the new
        request id and shows a success toast, then the opening link refreshes
        the recent-requests zone on its own GET. Without the runtime the same
        builder falls back to a redirect to the per-request audit page, so
        the no-JS path lands on the result just as before.
        """
        access_request = AccessRequest.objects.create(**cleaned_data)
        request.session["access_request_just_created"] = access_request.pk
        request.session.modified = True
        return (
            Patches(request)
            .layer_close(result={"id": access_request.pk})
            .toast("Access request submitted", variant="success")
            .response(fallback=f"/request/{access_request.pk}/audit/?just=1")
        )
