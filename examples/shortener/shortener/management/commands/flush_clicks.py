from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from shortener.cache import flush_clicks


class Command(BaseCommand):
    help = "Persist cached click counters into the database."

    def handle(self, *_args: Any, **_options: Any) -> None:  # noqa: ANN401
        """Persist cached counters and print a summary line."""
        total = flush_clicks()
        self.stdout.write(self.style.SUCCESS(f"flushed {total} clicks"))
