import subprocess
import sys

from tests.django_setup import PROJECT_ROOT


class TestBareImport:
    """`next.conf` imports before Django is set up, the way a settings module does."""

    def test_next_conf_imports_in_a_fresh_interpreter(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import next.conf\nfrom next.conf import extend_default_backend\n",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
