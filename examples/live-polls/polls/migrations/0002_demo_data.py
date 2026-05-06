from django.db import migrations


DEMO_POLLS = [
    {
        "question": "Tabs or spaces?",
        "choices": ["Tabs", "Spaces"],
    },
    {
        "question": "Vim or Emacs?",
        "choices": ["Vim", "Emacs"],
    },
]


def seed(apps, _schema_editor):
    """Insert two demo polls so the index is never empty on a fresh database."""
    poll_model = apps.get_model("polls", "Poll")
    choice_model = apps.get_model("polls", "Choice")
    for poll_data in DEMO_POLLS:
        poll, created = poll_model.objects.get_or_create(
            question=poll_data["question"]
        )
        if not created:
            continue
        for text in poll_data["choices"]:
            choice_model.objects.create(poll=poll, text=text)


def unseed(apps, _schema_editor):
    """Remove demo polls on rollback. Choices cascade with the poll."""
    poll_model = apps.get_model("polls", "Poll")
    poll_model.objects.filter(
        question__in=[p["question"] for p in DEMO_POLLS]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("polls", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
