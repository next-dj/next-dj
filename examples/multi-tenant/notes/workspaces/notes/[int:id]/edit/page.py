from typing import Any

from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
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


def get_owned_note(tenant: object, note_id: int) -> Note:
    """Return the note for `tenant` or raise 404 to keep tenants isolated."""
    return get_object_or_404(Note, pk=note_id, tenant=tenant)


class NoteEditForm(Form):
    note_id = django_forms.IntegerField(widget=django_forms.HiddenInput)
    title = django_forms.CharField(
        max_length=160,
        widget=django_forms.TextInput(attrs={"class": INPUT_CLASS}),
    )
    body = django_forms.CharField(
        required=False,
        widget=django_forms.Textarea(attrs={"class": TEXTAREA_CLASS}),
    )

    @classmethod
    def get_initial(
        cls,
        request: HttpRequest,
        id: int | None = None,  # noqa: A002
    ) -> dict[str, Any]:
        """Seed the form from the existing note when called during a GET render."""
        tenant = getattr(request, "tenant", None)
        if tenant is None or id is None:
            return {}
        note = get_owned_note(tenant, id)
        return {"note_id": note.pk, "title": note.title, "body": note.body}


@context("note")
def note(active_tenant: DTenant, id: int) -> Note:  # noqa: A002
    """Return the note iff it belongs to the active tenant."""
    return get_owned_note(active_tenant, id)


@action("note_edit", namespace="notes", form_class=NoteEditForm)
def note_edit(
    form: NoteEditForm,
    active_tenant: DTenant,
) -> HttpResponseRedirect:
    """Persist the new title and body, scoped to the active tenant."""
    note_id = form.cleaned_data["note_id"]
    note_obj = get_owned_note(active_tenant, note_id)
    note_obj.title = form.cleaned_data["title"]
    note_obj.body = form.cleaned_data.get("body", "")
    note_obj.save()
    return HttpResponseRedirect(
        reverse("next:page_notes_int_id_edit", kwargs={"id": note_obj.pk}),
    )
