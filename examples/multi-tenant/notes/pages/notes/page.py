from notes.models import Note, Tenant

from next import Depends, context
from next.pages import MetadataDict


metadata: MetadataDict = {"title": "Notes"}


@context("notes")
def notes(tenant: Tenant = Depends("scope")) -> list[Note]:
    """Return every note that belongs to the active tenant."""
    return list(Note.objects.visible_to(tenant))
