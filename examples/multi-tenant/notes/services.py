from django.db import transaction

from notes.models import Note, Tenant


def create_note(tenant: Tenant, *, title: str, body: str) -> Note:
    """Create a note owned by `tenant`."""
    note = Note(tenant=tenant, title=title, body=body)
    note.full_clean()
    note.save()
    return note


def update_note(tenant: Tenant, *, note_id: int, title: str, body: str) -> Note:
    """Rewrite a note `tenant` owns, loading it under a lock through `visible_to`."""
    with transaction.atomic():
        note = Note.objects.visible_to(tenant).select_for_update().get(pk=note_id)
        note.title = title
        note.body = body
        note.full_clean()
        note.save()
    return note
