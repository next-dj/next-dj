from django.db import migrations


LOCKED_TITLE = "Status update"


def lock(apps, schema_editor):  # noqa: ARG001
    """Lock one Acme note so the editor demonstrates the object-level guard."""
    note_model = apps.get_model("notes", "Note")
    note_model.objects.filter(
        tenant__slug="acme",
        title=LOCKED_TITLE,
    ).update(locked=True)


def unlock(apps, schema_editor):  # noqa: ARG001
    """Clear the lock flag on rollback."""
    note_model = apps.get_model("notes", "Note")
    note_model.objects.filter(
        tenant__slug="acme",
        title=LOCKED_TITLE,
    ).update(locked=False)


class Migration(migrations.Migration):
    dependencies = [
        ("notes", "0003_note_locked_tenant_is_active"),
    ]

    operations = [
        migrations.RunPython(lock, unlock),
    ]
