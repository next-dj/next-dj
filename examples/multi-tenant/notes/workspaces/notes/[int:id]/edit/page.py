from typing import ClassVar

from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from notes.access import get_active_tenant
from notes.models import Note
from notes.providers import DTenant

from next.forms import ModelForm
from next.pages import context


INPUT_CLASS = (
    "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm "
    "text-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-400"
)
TEXTAREA_CLASS = INPUT_CLASS + " min-h-[200px] font-mono"


def get_owned_note(tenant: object, note_id: int) -> Note:
    """Return the note for `tenant` or raise 404 to keep tenants isolated."""
    return get_object_or_404(Note, pk=note_id, tenant=tenant)


class NoteEditForm(ModelForm):
    class Meta:
        model = Note
        fields: ClassVar = ["title", "body"]
        widgets: ClassVar = {
            "title": django_forms.TextInput(attrs={"class": INPUT_CLASS}),
            "body": django_forms.Textarea(attrs={"class": TEXTAREA_CLASS}),
        }

    @classmethod
    def get_initial(cls, request: HttpRequest, id: int | None = None) -> object:  # noqa: A002
        """Load the tenant-owned note addressed by the URL, or raise 404."""
        return get_owned_note(get_active_tenant(request), id)

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Persist edits and redirect back to the note editor."""
        self.save()
        return HttpResponseRedirect(
            reverse("next:page_notes_int_id_edit", kwargs={"id": self.instance.pk}),
        )


@context("note")
def note(active_tenant: DTenant, id: int) -> Note:  # noqa: A002
    """Return the note iff it belongs to the active tenant."""
    return get_owned_note(active_tenant, id)
