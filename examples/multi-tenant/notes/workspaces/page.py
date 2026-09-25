from typing import TYPE_CHECKING

from notes.models import Note
from notes.providers import DTenant

from next import context, page
from next.pages import MetadataDict


if TYPE_CHECKING:
    from notes.models import Tenant


@page.metadata(inherit=True)
def workspace_meta(active_tenant: DTenant) -> MetadataDict:
    """Brand every tab with the tenant and keep its workspace out of the index."""
    return {
        "site_name": active_tenant.name,
        "title": {"default": active_tenant.name},
        "robots": {"index": False},
    }


@context("tenant", inherit_context=True)
def tenant(active_tenant: DTenant) -> "Tenant":
    """Expose the active tenant under `tenant` to every workspace page."""
    return active_tenant


@context("recent_notes")
def recent_notes(active_tenant: DTenant) -> list[Note]:
    """Return the five most recently updated notes for the landing card."""
    return list(Note.objects.filter(tenant=active_tenant)[:5])
