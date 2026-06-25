from __future__ import annotations

from django.core.management.base import BaseCommand

from shortener.cache import flush_clicks


class Command(BaseCommand):
    help = "Persist cached click counters into the database."

    def handle(self, *_args: object, **_options: object) -> None:
        """Persist cached counters and print a summary line."""
        total = flush_clicks()
        self.stdout.write(self.style.SUCCESS(f"flushed {total} clicks"))
