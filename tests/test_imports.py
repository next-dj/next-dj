import os
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.django_setup import PROJECT_ROOT


_EARLY_APP_MODULE = textwrap.dedent(
    """
    from django.apps import AppConfig

    from next.seo import Rule
    from next.signals import page_rendered
    from next.static import default_kinds


    class EarlyConfig(AppConfig):
        name = "earlyapp"
    """
)


def _next_module_names() -> list[str]:
    names = []
    for path in sorted((PROJECT_ROOT / "next").rglob("*.py")):
        parts = path.relative_to(PROJECT_ROOT).with_suffix("").parts
        names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


def _failure(completed: subprocess.CompletedProcess[str]) -> str | None:
    return None if completed.returncode == 0 else completed.stderr


def _import_first(name: str) -> str | None:
    """Import `name` before any other framework module, answering the error if any."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import importlib\n"
                "import os\n"
                "from django.conf import settings\n"
                "from tests.django_setup import build_test_settings\n"
                "settings.configure(**build_test_settings())\n"
                "importlib.import_module(os.environ['NEXT_IMPORT_FIRST'])\n"
            ),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "NEXT_IMPORT_FIRST": name},
    )
    return _failure(completed)


def _setup_with_early_app(app_parent: Path) -> str | None:
    """Run `django.setup()` with `earlyapp` listed ahead of `next`."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import django\n"
                "from django.conf import settings\n"
                "from tests.django_setup import build_test_settings\n"
                "config = build_test_settings()\n"
                "apps = list(config['INSTALLED_APPS'])\n"
                "apps.insert(apps.index('next'), 'earlyapp')\n"
                "settings.configure(**{**config, 'INSTALLED_APPS': apps})\n"
                "django.setup()\n"
            ),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(app_parent), "."])},
    )
    return _failure(completed)


class TestImportOrder:
    """Every framework module imports first in a fresh interpreter, whatever it is."""

    def test_every_module_imports_before_any_other(self) -> None:
        names = _next_module_names()
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
            errors = list(pool.map(_import_first, names))
        failures = {
            name: error.strip().splitlines()[-1]
            for name, error in zip(names, errors, strict=True)
            if error is not None
        }
        assert failures == {}

    def test_an_app_listed_before_next_imports_its_areas(self, tmp_path: Path) -> None:
        package = tmp_path / "earlyapp"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "apps.py").write_text(_EARLY_APP_MODULE)
        assert _setup_with_early_app(tmp_path) is None
