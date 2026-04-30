from typing import Any

from django import forms as django_forms
from django.http import HttpResponseRedirect
from django.urls import reverse
from notes.models import Note
from notes.providers import DTenant

from next.forms import Form, action
from next.pages import context


INPUT_CLASS = (
    "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm "
    "text-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-400"
)
TEXTAREA_CLASS = INPUT_CLASS + " min-h-[200px] font-mono"


class NoteCreateForm(Form):
    title = django_forms.CharField(
        max_length=160,
        widget=django_forms.TextInput(
            attrs={"class": INPUT_CLASS, "placeholder": "Note title"},
        ),
    )
    body = django_forms.CharField(
        required=False,
        widget=django_forms.Textarea(
            attrs={"class": TEXTAREA_CLASS, "placeholder": "# Markdown body"},
        ),
    )

    @classmethod
    def get_initial(cls) -> dict[str, Any]:
        """Empty initial state for the create form."""
        return {}


@context("draft_body")
def draft_body() -> str:
    """Seed an empty body so the markdown preview pane renders on first paint."""
    return ""


@action("note_create", namespace="notes", form_class=NoteCreateForm)
def note_create(
    form: NoteCreateForm,
    active_tenant: DTenant,
) -> HttpResponseRedirect:
    """Create a new note scoped to the active tenant and redirect to its editor."""
    note_obj = Note.objects.create(
        tenant=active_tenant,
        title=form.cleaned_data["title"],
        body=form.cleaned_data.get("body", ""),
    )
    return HttpResponseRedirect(
        reverse("next:page_notes_int_id_edit", kwargs={"id": note_obj.pk}),
    )
