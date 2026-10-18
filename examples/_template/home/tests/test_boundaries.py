from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase


class BoundariesTests(SimpleTestCase):
    def test_the_apps_keep_their_boundaries(self) -> None:
        call_command(
            "check", "--tag", "boundaries", "--fail-level", "WARNING", stdout=StringIO()
        )
