from django.urls import reverse
from notes.models import Note

from next.components import component


EXCERPT_LIMIT = 140


@component.context("excerpt")
def excerpt(note: Note) -> str:
    """Return a short preview of the body for the card."""
    text = note.body or ""
    if len(text) <= EXCERPT_LIMIT:
        return text
    return text[: EXCERPT_LIMIT - 1].rstrip() + "…"


@component.context("edit_url")
def edit_url(note: Note) -> str:
    """Return the absolute edit URL for the note."""
    return reverse("next:page_notes_int_id_edit", kwargs={"id": note.pk})
