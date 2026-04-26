from typing import Any, ClassVar

from access.models import AccessRequest
from access.steps import STEP_ORDER, normalise_step
from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import Form, action
from next.pages import context


SESSION_KEY = "access_request"
LAST_CREATED_KEY = "access_request_just_created"

_INPUT_CLASS = (
    "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm "
    "text-slate-900 placeholder:text-slate-400 focus:outline-none "
    "focus:ring-2 focus:ring-slate-400 focus:border-transparent"
)
_INPUT_ATTRS = {"class": _INPUT_CLASS}
_TEXTAREA_ATTRS = {"class": _INPUT_CLASS + " resize-none", "rows": "4"}


STEP_FIELD_BUILDERS: dict[str, Any] = {
    "applicant": lambda: {
        "full_name": django_forms.CharField(
            max_length=120,
            widget=django_forms.TextInput(attrs=_INPUT_ATTRS),
        ),
        "email": django_forms.EmailField(
            widget=django_forms.EmailInput(attrs=_INPUT_ATTRS),
        ),
        "team": django_forms.CharField(
            max_length=60,
            widget=django_forms.TextInput(attrs=_INPUT_ATTRS),
        ),
    },
    "justification": lambda: {
        "project_slug": django_forms.SlugField(
            max_length=64,
            widget=django_forms.TextInput(attrs=_INPUT_ATTRS),
        ),
        "reason": django_forms.CharField(
            widget=django_forms.Textarea(attrs=_TEXTAREA_ATTRS),
        ),
        "expires_in_days": django_forms.IntegerField(
            min_value=1,
            max_value=30,
            widget=django_forms.NumberInput(attrs=_INPUT_ATTRS),
        ),
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
        return normalise_step(value)

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
        draft = dict(request.session.get(SESSION_KEY, {}))
        draft["step"] = normalise_step(step)
        return draft


@context("current_step")
def current_step(step: str = "applicant") -> str:
    return normalise_step(step)


@context("draft")
def draft(request: HttpRequest) -> dict[str, Any]:
    return dict(request.session.get(SESSION_KEY, {}))


@action("request_step", namespace="access", form_class=RequestStepForm)
def request_step(
    form: RequestStepForm,
    request: HttpRequest,
) -> HttpResponseRedirect:
    """Persist the current step into the session, then move on or commit."""
    step = form.cleaned_data["step"]
    draft_data = dict(request.session.get(SESSION_KEY, {}))
    for key, value in form.cleaned_data.items():
        if key == "step":
            continue
        draft_data[key] = value
    request.session[SESSION_KEY] = draft_data
    request.session.modified = True
    if step != STEP_ORDER[-1]:
        next_step = STEP_ORDER[STEP_ORDER.index(step) + 1]
        return HttpResponseRedirect(f"/request/{next_step}/")
    access_request = AccessRequest.objects.create(
        full_name=draft_data["full_name"],
        email=draft_data["email"],
        team=draft_data["team"],
        project_slug=draft_data["project_slug"],
        reason=draft_data["reason"],
        expires_in_days=draft_data["expires_in_days"],
    )
    request.session.pop(SESSION_KEY, None)
    request.session[LAST_CREATED_KEY] = access_request.pk
    request.session.modified = True
    return HttpResponseRedirect(f"/request/{access_request.pk}/audit/?just=1")
