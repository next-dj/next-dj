from typing import ClassVar

from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.safestring import SafeString
from notes.access import get_active_tenant
from notes.markdown_render import render_markdown
from notes.models import Note
from notes.providers import DTenant

from next.forms import ComponentWidget, ModelForm, PermissionOutcome
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
    def check_permissions(cls, tenant: DTenant) -> PermissionOutcome:
        """View-level gate. A suspended tenant may not edit any note."""
        return tenant.is_active

    @classmethod
    def get_initial(cls, request: HttpRequest, note_id: int | None = None) -> object:
        """Load the tenant-owned note addressed by the URL, or raise 404."""
        return get_owned_note(get_active_tenant(request), note_id)

    def has_object_permission(self) -> PermissionOutcome:
        """Object-level gate. A locked note is read-only even for its tenant."""
        return not self.instance.locked

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Persist edits and redirect back to the note editor."""
        self.save()
        return HttpResponseRedirect(
            reverse(
                "next:page_notes_int_note_id_edit",
                kwargs={"note_id": self.instance.pk},
            ),
        )


@context("note")
def note(active_tenant: DTenant, note_id: int) -> Note:
    """Return the note iff it belongs to the active tenant."""
    return get_owned_note(active_tenant, note_id)


@context("preview_html")
def preview_html(note: Note) -> SafeString:
    """Render the note body so the preview pane matches it on first paint."""
    return render_markdown(note.body)
