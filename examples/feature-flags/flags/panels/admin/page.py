from django import forms
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseRedirect
from flags.models import Flag
from flags.providers import WRITE_GATE_FLAG, FlagService

from next.deps import Depends
from next.forms import Form
from next.pages import context


class BulkToggleForm(Form):
    enabled_names = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "hidden"}),
    )

    class Meta:
        success_url = "/admin/"
        success_message = "Flag toggles saved."

    @classmethod
    def check_permissions(cls, flags: FlagService = Depends("flag_service")) -> None:
        """Deny the toggle action while the `admin_writes` gate flag is off."""
        if not flags.is_enabled(WRITE_GATE_FLAG):
            raise PermissionDenied

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Populate the choices from the current set of flag names."""
        super().__init__(*args, **kwargs)
        self.fields["enabled_names"].choices = [
            (name, name) for name in Flag.objects.values_list("name", flat=True)
        ]

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Toggle each flag to match the submitted set, then follow the contract."""
        enabled_names = set(self.cleaned_data["enabled_names"])
        for flag in Flag.objects.all():
            should_be_on = flag.name in enabled_names
            if flag.enabled != should_be_on:
                flag.enabled = should_be_on
                flag.save(update_fields=["enabled", "updated_at"])
        return super().on_valid(request)


@context("flags")
def flags() -> list[Flag]:
    return list(Flag.objects.all())


@context("flash_messages")
def flash_messages(request: HttpRequest) -> list[str]:
    """Drain the pending Meta.success_message flashes for the admin banner."""
    return [str(m) for m in get_messages(request)]
