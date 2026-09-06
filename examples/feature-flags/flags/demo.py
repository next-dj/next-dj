from flags.models import Flag


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


def seed_demo() -> None:
    """Create the demo flags so a fresh database has something to toggle."""
    for flag_data in DEMO_FLAGS:
        Flag.objects.get_or_create(
            name=flag_data["name"],
            defaults={
                "label": flag_data["label"],
                "description": flag_data["description"],
                "enabled": flag_data["enabled"],
            },
        )
