from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect
from notes.models import Note
from notes.providers import DTenant

from next.forms import ComponentWidget, Form
from next.pages import MetadataDict
from next.urls import page_reverse


metadata: MetadataDict = {"title": "New note"}


class NoteCreateForm(Form):
    title = django_forms.CharField(
        max_length=160, widget=ComponentWidget("input", placeholder="Note title")
    )
    body = django_forms.CharField(
        required=False,
        widget=ComponentWidget(
            "markdown_textarea", placeholder="# Markdown body", rows=8
        ),
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
            page_reverse("notes/[int:note_id]/edit", note_id=note_obj.pk)
        )
