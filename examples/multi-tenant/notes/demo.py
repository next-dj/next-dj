from notes.models import Note, Tenant


DEMO_TENANTS = [
    {
        "slug": "acme",
        "name": "Acme Industries",
        "primary_color": "#2563eb",
        "notes": [
            {
                "title": "Welcome to Acme",
                "body": (
                    "# Acme onboarding\n\n"
                    "This workspace is **scoped** to the Acme tenant. "
                    "Notes here are invisible to Globex.\n\n"
                    "- Try `?tenant=globex` to switch.\n"
                    "- Send `X-Tenant: globex` from `curl` for the production path.\n"
                ),
            },
            {
                "title": "Status update",
                "body": "Quarterly metrics are looking healthy across the board.",
                "locked": True,
            },
        ],
    },
    {
        "slug": "globex",
        "name": "Globex Corporation",
        "primary_color": "#16a34a",
        "notes": [
            {
                "title": "Globex roadmap",
                "body": "## Roadmap\n\n1. Phase one\n2. Phase two\n3. Phase three\n",
            }
        ],
    },
]


def seed_demo() -> None:
    """Create the demo tenants and notes for the browser and curl walkthroughs.

    One Acme note ships locked so the editor has a row that exercises the
    object-level guard.
    """
    for tenant_data in DEMO_TENANTS:
        tenant, _ = Tenant.objects.get_or_create(
            slug=tenant_data["slug"],
            defaults={
                "name": tenant_data["name"],
                "primary_color": tenant_data["primary_color"],
            },
        )
        for note_data in tenant_data["notes"]:
            Note.objects.get_or_create(
                tenant=tenant,
                title=note_data["title"],
                defaults={
                    "body": note_data["body"],
                    "locked": note_data.get("locked", False),
                },
            )
