from django.db import migrations


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
            },
        ],
    },
]


def seed(apps, schema_editor):  # noqa: ARG001
    """Insert demo tenants and notes for both browser and curl walkthroughs."""
    tenant_model = apps.get_model("notes", "Tenant")
    note_model = apps.get_model("notes", "Note")
    for tenant_data in DEMO_TENANTS:
        tenant_obj, _ = tenant_model.objects.get_or_create(
            slug=tenant_data["slug"],
            defaults={
                "name": tenant_data["name"],
                "primary_color": tenant_data["primary_color"],
            },
        )
        for note_data in tenant_data["notes"]:
            note_model.objects.get_or_create(
                tenant=tenant_obj,
                title=note_data["title"],
                defaults={"body": note_data["body"]},
            )


def unseed(apps, schema_editor):  # noqa: ARG001
    """Remove demo tenants on rollback (cascade clears their notes)."""
    tenant_model = apps.get_model("notes", "Tenant")
    tenant_model.objects.filter(
        slug__in=[t["slug"] for t in DEMO_TENANTS]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("notes", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
