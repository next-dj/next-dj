"""`manage.py check`, which also runs the boundary checks when `--tag boundaries` asks.

Their registry loads on first use, so a run without the tag never loads the engine.
"""

from typing import Any, override

from django.core.checks.registry import registry
from django.core.management.commands import check

from next import boundaries, checks
from next.checks import BOUNDARIES, NEXT


class Command(check.Command):
    """Run the system checks, the boundary ones only under their own tag."""

    @override
    def handle(self, *app_labels: list[str], **options: Any) -> None:
        """Lend the boundary checks to the global registry for this run alone.

        A run with the `next` tag already holds the mode check, so it is not lent twice.
        """
        tags = options.get("tags") or ()
        if not options.get("list_tags") and BOUNDARIES not in tags:
            super().handle(*app_labels, **options)
            return
        held = {checks.check_boundaries_mode} if NEXT in tags else set()
        offered = set(boundaries.boundary_checks.get_checks()) - held
        lent = offered - registry.registered_checks
        registry.registered_checks |= lent
        try:
            super().handle(*app_labels, **options)
        finally:
            registry.registered_checks -= lent
