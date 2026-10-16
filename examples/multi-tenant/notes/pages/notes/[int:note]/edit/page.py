from typing import ClassVar

from django.http import HttpRequest, HttpResponseRedirect
from notes.models import Note, Tenant

from next import Depends, context, page
from next.deps import DObject
from next.forms import ComponentWidget, ModelForm, PermissionOutcome
from next.pages import MetadataDict
from next.urls import page_reverse


class NoteEditForm(ModelForm):
    class Meta:
        model = Note
        fields: ClassVar = ["title", "body"]
        widgets: ClassVar = {
            "title": ComponentWidget("input"),
            "body": ComponentWidget("markdown_textarea", rows=8),
        }

    @classmethod
    def check_permissions(cls, tenant: Tenant = Depends("scope")) -> PermissionOutcome:
        """View-level gate. A suspended tenant may not edit any note."""
        return tenant.is_active

    @classmethod
    def get_initial(cls, note: DObject[Note]) -> Note:
        """Bind the note the URL names, which only loads for the tenant that owns it."""
        return note

    def has_object_permission(self) -> PermissionOutcome:
        """Object-level gate. A locked note is read-only even for its tenant."""
        return not self.instance.locked

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Persist edits and redirect back to the note editor."""
        self.save()
        return HttpResponseRedirect(
            page_reverse("notes/[int:note]/edit", note=self.instance.pk)
        )


@context("note")
def note(note: DObject[Note]) -> Note:
    """Expose the note the URL names to the template, scoped to the active tenant."""
    return note


@page.metadata
def note_meta(note: DObject[Note]) -> MetadataDict:
    """Title the editor after the note, the same row the `note` context loaded."""
    return {"title": note.title}
