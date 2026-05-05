from django.db import migrations


DEMO_BOARDS = [
    {
        "title": "Engineering roadmap",
        "slug": "engineering-roadmap",
        "archived": False,
        "columns": [
            {
                "title": "Backlog",
                "wip_limit": None,
                "cards": [
                    {"title": "Audit static pipeline", "body": "Review collector + manager."},
                    {"title": "Document KindRegistry", "body": "Public API guide."},
                ],
            },
            {
                "title": "In progress",
                "wip_limit": 2,
                "cards": [
                    {"title": "Refactor discovery", "body": "Iterate over kinds."},
                ],
            },
            {
                "title": "Review",
                "wip_limit": 3,
                "cards": [],
            },
            {
                "title": "Done",
                "wip_limit": None,
                "cards": [
                    {"title": "Wire defaults bootstrap", "body": "register_defaults()."},
                ],
            },
        ],
    },
    {
        "title": "Marketing launch",
        "slug": "marketing-launch",
        "archived": False,
        "columns": [
            {
                "title": "Ideas",
                "wip_limit": None,
                "cards": [
                    {"title": "Tagline draft", "body": "Pithy and informative."},
                ],
            },
            {
                "title": "Scheduled",
                "wip_limit": 4,
                "cards": [
                    {"title": "Blog post", "body": "Tutorial walkthrough."},
                    {"title": "Demo video", "body": "Two-minute screencast."},
                ],
            },
        ],
    },
    {
        "title": "Old experiments",
        "slug": "old-experiments",
        "archived": True,
        "columns": [],
    },
]


def seed(apps, schema_editor):  # noqa: ARG001
    """Insert two active demo boards plus one archived board."""
    board_model = apps.get_model("kanban", "Board")
    column_model = apps.get_model("kanban", "Column")
    card_model = apps.get_model("kanban", "Card")
    for board_data in DEMO_BOARDS:
        board, created = board_model.objects.get_or_create(
            slug=board_data["slug"],
            defaults={
                "title": board_data["title"],
                "archived": board_data["archived"],
            },
        )
        if not created:
            continue
        for col_index, col_data in enumerate(board_data["columns"]):
            column = column_model.objects.create(
                board=board,
                title=col_data["title"],
                position=col_index,
                wip_limit=col_data["wip_limit"],
            )
            for card_index, card_data in enumerate(col_data["cards"]):
                card_model.objects.create(
                    column=column,
                    title=card_data["title"],
                    body=card_data["body"],
                    position=card_index,
                )


def unseed(apps, schema_editor):  # noqa: ARG001
    """Remove demo boards on rollback (cascade clears columns and cards)."""
    board_model = apps.get_model("kanban", "Board")
    board_model.objects.filter(
        slug__in=[b["slug"] for b in DEMO_BOARDS]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("kanban", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
