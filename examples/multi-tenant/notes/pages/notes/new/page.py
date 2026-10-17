from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect
from notes.models import Note, Tenant

from next import Depends, page
from next.forms import ComponentWidget, Form
from next.pages import MetadataDict
from next.urls import page_reverse


metadata: MetadataDict = {"title": "New note"}

page.public()


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
        self, request: HttpRequest, tenant: Tenant = Depends("scope")
    ) -> HttpResponseRedirect:
        """Create a new note scoped to the active tenant and redirect to its editor."""
        note_obj = Note.objects.create(
            tenant=tenant,
            title=self.cleaned_data["title"],
            body=self.cleaned_data.get("body", ""),
        )
        return HttpResponseRedirect(
            page_reverse("notes/[int:note]/edit", note=note_obj.pk)
        )
