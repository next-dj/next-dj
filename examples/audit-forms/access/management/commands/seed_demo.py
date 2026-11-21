from django.core.management.base import BaseCommand

from access.demo import seed_demo


class Command(BaseCommand):
    help = "Fill an empty audit log with the demo requests and their rows."

    def handle(self, *args: object, **options: object) -> None:
        """Seed the demo dataset and report it on stdout."""
        seed_demo()
        self.stdout.write(self.style.SUCCESS("Demo audit log seeded."))
