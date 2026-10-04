import os.path
import subprocess
import sys

import pytest

from next.conf.imports import import_callable
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


class TestImportCallable:
    """A dotted path answers the callable it names, `None` for anything else."""

    def test_a_dotted_path_naming_a_callable_imports_it(self) -> None:
        assert import_callable("os.path.join") is os.path.join

    @pytest.mark.parametrize(
        "dotted", ["os.path.sep", "nowhere.at_all"], ids=["not_callable", "missing"]
    )
    def test_anything_else_is_none(self, dotted: str) -> None:
        assert import_callable(dotted) is None
