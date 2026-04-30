from notes.models import Note
from notes.providers import DTenant

from next.pages import context


@context("tenant", inherit_context=True)
def tenant(active_tenant: DTenant) -> object:
    """Expose the active tenant under `tenant` to every workspace page."""
    return active_tenant


@context("recent_notes")
def recent_notes(active_tenant: DTenant) -> list[Note]:
    """Return the five most recently updated notes for the landing card."""
    return list(Note.objects.filter(tenant=active_tenant)[:5])
