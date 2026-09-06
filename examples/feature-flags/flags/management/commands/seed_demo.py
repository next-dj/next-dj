from django.core.management.base import BaseCommand

from flags.demo import seed_demo


class Command(BaseCommand):
    help = "Create the demo feature flags."

    def handle(self, *args: object, **options: object) -> None:
        """Seed the demo dataset and report it on stdout."""
        seed_demo()
        self.stdout.write(self.style.SUCCESS("Demo flags seeded."))
