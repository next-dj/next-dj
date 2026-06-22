from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils.safestring import SafeString
from notes.markdown_render import render_markdown
from notes.models import Note
from notes.providers import DTenant

from next.forms import ComponentWidget, Form
from next.pages import context


class NoteCreateForm(Form):
    title = django_forms.CharField(
        max_length=160,
        widget=ComponentWidget("input", placeholder="Note title"),
    )
    body = django_forms.CharField(
        required=False,
        widget=ComponentWidget("textarea", placeholder="# Markdown body", rows=8),
    )

    def on_valid(
        self, request: HttpRequest, active_tenant: DTenant
    ) -> HttpResponseRedirect:
        """Create a new note scoped to the active tenant and redirect to its editor."""
        note_obj = Note.objects.create(
            tenant=active_tenant,
            title=self.cleaned_data["title"],
            body=self.cleaned_data.get("body", ""),
        )
        return HttpResponseRedirect(
            reverse(
                "next:page_notes_int_note_id_edit",
                kwargs={"note_id": note_obj.pk},
            ),
        )


@context("preview_html")
def preview_html() -> SafeString:
    """Render the empty draft so the preview pane has its first-paint placeholder."""
    return render_markdown("")
