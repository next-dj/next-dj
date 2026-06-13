from typing import ClassVar

from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from notes.access import get_active_tenant
from notes.models import Note
from notes.providers import DTenant

from next.forms import ComponentWidget, ModelForm
from next.pages import context


def get_owned_note(tenant: object, note_id: int) -> Note:
    """Return the note for `tenant` or raise 404 to keep tenants isolated."""
    return get_object_or_404(Note, pk=note_id, tenant=tenant)


class NoteEditForm(ModelForm):
    class Meta:
        model = Note
        fields: ClassVar = ["title", "body"]
        widgets: ClassVar = {
            "title": ComponentWidget("input"),
            "body": ComponentWidget("textarea", rows=8),
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
