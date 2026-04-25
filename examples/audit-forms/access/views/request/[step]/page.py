from typing import Any, ClassVar

from access.models import AccessRequest
from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import Form, action
from next.pages import context


STEP_ORDER: list[str] = ["applicant", "justification", "review"]
STEP_LABEL: dict[str, str] = {
    "applicant": "Applicant",
    "justification": "Justification",
    "review": "Review",
}
STEP_FIELD_BUILDERS: dict[str, Any] = {
    "applicant": lambda: {
        "full_name": django_forms.CharField(max_length=120),
        "email": django_forms.EmailField(),
        "team": django_forms.CharField(max_length=60),
    },
    "justification": lambda: {
        "project_slug": django_forms.SlugField(max_length=64),
        "reason": django_forms.CharField(widget=django_forms.Textarea),
        "expires_in_days": django_forms.IntegerField(min_value=1, max_value=30),
    },
    "review": dict,
}


class RequestStepForm(Form):
    step = django_forms.CharField(widget=django_forms.HiddenInput)

    field_order: ClassVar[list[str]] = ["step"]

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Attach per-step fields based on the bound or initial `step` value."""
        super().__init__(*args, **kwargs)
        active = self._derive_step()
        for name, field in STEP_FIELD_BUILDERS[active]().items():
            self.fields[name] = field
        self.active_step = active

    def _derive_step(self) -> str:
        value = self.data.get("step") if self.is_bound else self.initial.get("step")
        if value not in STEP_ORDER:
            return STEP_ORDER[0]
        return value

    def clean_step(self) -> str:
        """Reject any step value that is not part of the canonical order."""
        value = self.cleaned_data.get("step", "")
        if value not in STEP_ORDER:
            msg = "Unknown step."
            raise django_forms.ValidationError(msg)
        return value

    @classmethod
    def get_initial(
        cls,
        request: HttpRequest,
        step: str = "applicant",
    ) -> dict[str, Any]:
        """Seed the form from `request.session` so previous steps repopulate."""
        if step not in STEP_ORDER:
            step = STEP_ORDER[0]
        draft = dict(request.session.get("access_request", {}))
        draft["step"] = step
        return draft


@context("current_step")
def current_step(step: str = "applicant") -> str:
    if step not in STEP_ORDER:
        return STEP_ORDER[0]
    return step


@context("step_index")
def step_index(step: str = "applicant") -> int:
    if step not in STEP_ORDER:
        return 1
    return STEP_ORDER.index(step) + 1


@context("step_total")
def step_total() -> int:
    return len(STEP_ORDER)


@context("progress_steps")
def progress_steps(step: str = "applicant") -> list[dict[str, Any]]:
    """Return step descriptors for the `progress_bar` composite component."""
    if step not in STEP_ORDER:
        step = STEP_ORDER[0]
    current_index = STEP_ORDER.index(step)
    return [
        {
            "key": key,
            "label": STEP_LABEL[key],
            "index": index + 1,
            "status": _status_for(index, current_index),
        }
        for index, key in enumerate(STEP_ORDER)
    ]


def _status_for(index: int, current_index: int) -> str:
    if index < current_index:
        return "done"
    if index == current_index:
        return "current"
    return "upcoming"


@context("draft")
def draft(request: HttpRequest) -> dict[str, Any]:
    return dict(request.session.get("access_request", {}))


@action("request_step", namespace="access", form_class=RequestStepForm)
def request_step(
    form: RequestStepForm,
    request: HttpRequest,
) -> HttpResponseRedirect:
    """Persist the current step into the session, then move on or commit."""
    step = form.cleaned_data["step"]
    draft_data = dict(request.session.get("access_request", {}))
    for key, value in form.cleaned_data.items():
        if key == "step":
            continue
        draft_data[key] = value
    request.session["access_request"] = draft_data
    request.session.modified = True
    if step != STEP_ORDER[-1]:
        next_step = STEP_ORDER[STEP_ORDER.index(step) + 1]
        return HttpResponseRedirect(f"/request/{next_step}/")
    AccessRequest.objects.create(
        full_name=draft_data["full_name"],
        email=draft_data["email"],
        team=draft_data["team"],
        project_slug=draft_data["project_slug"],
        reason=draft_data["reason"],
        expires_in_days=int(draft_data["expires_in_days"]),
    )
    request.session.pop("access_request", None)
    return HttpResponseRedirect("/admin/audit/")
