from django.db import migrations


DEMO_FLAGS = [
    {
        "name": "beta_checkout",
        "label": "Beta checkout",
        "description": "Use the new checkout flow.",
        "enabled": True,
    },
    {
        "name": "dark_sidebar",
        "label": "Dark sidebar",
        "description": "Experimental dark-mode navigation.",
        "enabled": False,
    },
    {
        "name": "ai_suggestions",
        "label": "AI suggestions",
        "description": "Recommendations powered by the v2 model.",
        "enabled": False,
    },
    {
        "name": "admin_writes",
        "label": "Admin writes",
        "description": "Gate for the bulk-toggle action.",
        "enabled": True,
    },
]


def seed(apps, _schema_editor):
    """Insert the demo flags so a fresh database has something to toggle."""
    flag_model = apps.get_model("flags", "Flag")
    for flag_data in DEMO_FLAGS:
        flag_model.objects.get_or_create(
            name=flag_data["name"],
            defaults={
                "label": flag_data["label"],
                "description": flag_data["description"],
                "enabled": flag_data["enabled"],
            },
        )


def unseed(apps, _schema_editor):
    """Remove exactly the seeded flags on rollback and leave the rest alone."""
    flag_model = apps.get_model("flags", "Flag")
    flag_model.objects.filter(name__in=[f["name"] for f in DEMO_FLAGS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("flags", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
