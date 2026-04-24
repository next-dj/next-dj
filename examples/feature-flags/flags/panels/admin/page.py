from __future__ import annotations

from django import forms
from django.http import HttpResponseRedirect
from flags.models import Flag

from next.forms import Form, action
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


@context("flags")
def _flags() -> list[Flag]:
    return list(Flag.objects.all())


@action("bulk_toggle", form_class=BulkToggleForm)
def _bulk_toggle(form: BulkToggleForm) -> HttpResponseRedirect:
    enabled_names = set(form.cleaned_data["enabled_names"])
    for flag in Flag.objects.all():
        should_be_on = flag.name in enabled_names
        if flag.enabled != should_be_on:
            flag.enabled = should_be_on
            flag.save(update_fields=["enabled", "updated_at"])
    return HttpResponseRedirect("/admin/")
