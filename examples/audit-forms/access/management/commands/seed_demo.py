from django.core.management.base import BaseCommand

from access.demo import seed_demo


class Command(BaseCommand):
    help = "Fill an empty audit log with the demo requests and their rows."

    def handle(self, *args: object, **options: object) -> None:
        """Seed the demo dataset and report it on stdout."""
        rows = seed_demo()
        message = f"Demo audit log seeded with {rows} rows."
        self.stdout.write(self.style.SUCCESS(message))
