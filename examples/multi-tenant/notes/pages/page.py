from notes.models import Note, Tenant

from next import Depends, context, page
from next.pages import MetadataDict


@page.metadata(inherit=True)
def workspace_meta(tenant: Tenant = Depends("scope")) -> MetadataDict:
    """Brand every tab with the tenant and keep its workspace out of the index."""
    return {
        "site_name": tenant.name,
        "title": {"default": tenant.name},
        "robots": {"index": False},
    }


@context("tenant", inherit_context=True)
def tenant(active_tenant: Tenant = Depends("scope")) -> Tenant:
    """Expose the active tenant under `tenant` to every workspace page."""
    return active_tenant


@context("recent_notes")
def recent_notes(tenant: Tenant = Depends("scope")) -> list[Note]:
    """Return the five most recently updated notes for the landing card."""
    return list(Note.objects.visible_to(tenant)[:5])
