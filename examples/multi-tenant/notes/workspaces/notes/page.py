from notes.models import Note
from notes.providers import DTenant

from next.pages import context


@context("notes")
def notes(active_tenant: DTenant) -> list[Note]:
    """Return every note that belongs to the active tenant."""
    return list(Note.objects.filter(tenant=active_tenant))
