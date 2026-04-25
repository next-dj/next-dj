from __future__ import annotations

from flags.models import Flag

from next.pages import context


DEMO_FLAG_NAMES = ("beta_checkout", "dark_sidebar", "ai_suggestions")


@context("demo_flags")
def demo_flags() -> list[dict[str, object]]:
    known = {f.name: f for f in Flag.objects.filter(name__in=DEMO_FLAG_NAMES)}
    return [
        {
            "name": name,
            "label": getattr(known.get(name), "label", name.replace("_", " ").title()),
            "enabled": getattr(known.get(name), "enabled", False),
        }
        for name in DEMO_FLAG_NAMES
    ]
