from django import forms
from django.http import HttpRequest, HttpResponseRedirect
from flags.models import Flag

from next.forms import Form
from next.pages import context


class BulkToggleForm(Form):
    enabled_names = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "hidden"}),
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Populate the choices from the current set of flag names."""
        super().__init__(*args, **kwargs)
        self.fields["enabled_names"].choices = [
            (name, name) for name in Flag.objects.values_list("name", flat=True)
        ]

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Toggle each flag to match the submitted set and redirect back."""
        enabled_names = set(self.cleaned_data["enabled_names"])
        for flag in Flag.objects.all():
            should_be_on = flag.name in enabled_names
            if flag.enabled != should_be_on:
                flag.enabled = should_be_on
                flag.save(update_fields=["enabled", "updated_at"])
        return HttpResponseRedirect("/admin/")


@context("flags")
def flags() -> list[Flag]:
    return list(Flag.objects.all())
