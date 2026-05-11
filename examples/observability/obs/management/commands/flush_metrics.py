from django.core.management.base import BaseCommand

from obs.metrics import flush
from obs.models import MetricSnapshot


class Command(BaseCommand):
    help = "Persist cached counters into the MetricSnapshot table."

    def handle(self, *_args: object, **_options: object) -> None:
        """Drain every counter, persist the snapshot, and clear the cache."""
        rows = flush()
        if not rows:
            self.stdout.write("nothing to flush")
            return
        MetricSnapshot.objects.bulk_create(
            [
                MetricSnapshot(kind=kind, key=key, value=value)
                for kind, key, value in rows
            ]
        )
        self.stdout.write(self.style.SUCCESS(f"flushed {len(rows)} counters"))
