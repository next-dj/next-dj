from django import forms
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseRedirect
from flags import services
from flags.deps import WRITE_GATE_FLAG, FlagService
from flags.models import Flag

from next import Depends, context, page
from next.forms import Form
from next.pages import MetadataDict
from next.urls import page_reverse_lazy


metadata: MetadataDict = {"title": "Flag admin", "robots": {"index": False}}

page.public()


class BulkToggleForm(Form):
    enabled_names = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple(attrs={"class": "hidden"})
    )

    class Meta:
        success_url = page_reverse_lazy("admin")
        success_message = "Flag toggles saved."

    @classmethod
    def check_permissions(cls, flags: FlagService = Depends("flag_service")) -> None:
        """Deny the toggle action while the `admin_writes` gate flag is off."""
        if not flags.is_enabled(WRITE_GATE_FLAG):
            raise PermissionDenied

    def __init__(self, *args, **kwargs) -> None:
        """Populate the choices from the current set of flag names."""
        super().__init__(*args, **kwargs)
        self.fields["enabled_names"].choices = [
            (name, name) for name in services.flag_names()
        ]

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Toggle each flag to match the submitted set, then follow the contract."""
        services.set_enabled_flags(enabled_names=self.cleaned_data["enabled_names"])
        return super().on_valid(request)


@context("flags")
def flags() -> list[Flag]:
    return services.all_flags()
