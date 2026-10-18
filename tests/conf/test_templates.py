import os
import subprocess
import sys
from pathlib import Path

from django.core.management import call_command

import next.conf


_TEMPLATES = Path(next.conf.__file__).parent

_PROJECT_TEMPLATE = _TEMPLATES / "project_template"

_APP_TEMPLATE = _TEMPLATES / "app_template"

_INSTALLED = '    "home",\n]'

_BOUNDARY_TEST = "test_the_apps_keep_their_boundaries"


def _expected(template: Path, prefix: str, name: str = "") -> set[str]:
    return {
        f"{prefix}{path.relative_to(template).as_posix().removesuffix('-tpl')}".replace(
            "project_name/", f"{name}/"
        )
        for path in template.rglob("*")
        if path.is_file()
    }


def _generated(project: Path) -> set[str]:
    return {
        path.relative_to(project).as_posix()
        for path in project.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def _options(project: Path) -> dict[str, object]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "DJANGO_SETTINGS_MODULE"
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return {"cwd": project, "env": env, "capture_output": True, "text": True}


def _new_project(tmp_path: Path) -> Path:
    project = tmp_path / "boundsite"
    (project / "shop").mkdir(parents=True)
    call_command(
        "startproject", "boundsite", str(project), template=str(_PROJECT_TEMPLATE)
    )
    call_command("startapp", "shop", str(project / "shop"), template=str(_APP_TEMPLATE))
    settings = project / "boundsite" / "settings.py"
    text = settings.read_text()
    settings.write_text(text.replace(_INSTALLED, '    "home",\n    "shop",\n]'))
    return project


class TestProjectTemplates:
    """A project and an app made from the shipped templates start clean and strict."""

    def test_a_new_project_with_a_new_app(self, tmp_path) -> None:
        project = _new_project(tmp_path)
        text = (_PROJECT_TEMPLATE / "project_name" / "settings.py-tpl").read_text()
        generated = _generated(project)

        checked = subprocess.run(
            [sys.executable, "manage.py", "check", "--fail-level", "WARNING"],
            check=False,
            **_options(project),
        )
        migrated = subprocess.run(
            [sys.executable, "manage.py", "makemigrations", "--check", "--dry-run"],
            check=False,
            **_options(project),
        )
        tested = subprocess.run(
            [sys.executable, "manage.py", "test"], check=False, **_options(project)
        )

        assert generated == (
            _expected(_PROJECT_TEMPLATE, "", "boundsite")
            | _expected(_APP_TEMPLATE, "shop/")
        )
        assert [
            path.name
            for path in project.rglob("*.py")
            if "{{" in path.read_text() or "{%" in path.read_text()
        ] == []
        assert '"BOUNDARIES": "strict"' in text
        assert _INSTALLED in text
        assert (checked.returncode, checked.stderr) == (0, "")
        assert checked.stdout == "System check identified no issues (0 silenced).\n"
        assert (migrated.returncode, migrated.stdout, migrated.stderr) == (
            0,
            "No changes detected\n",
            "",
        )
        assert tested.returncode == 0, tested.stderr
        assert tested.stderr.splitlines()[-2:] == [
            "OK",
            "Destroying test database for alias 'default'...",
        ]


class TestStrictBoundaries:
    """The test the project template ships fails the run on a boundary finding."""

    def test_a_private_import_fails_the_home_test(self, tmp_path) -> None:
        project = _new_project(tmp_path)
        (project / "home" / "helpers.py").write_text(
            "from shop.forms import ItemForm\n"
        )

        tested = subprocess.run(
            [sys.executable, "manage.py", "test", "home"],
            check=False,
            **_options(project),
        )
        checked = subprocess.run(
            [sys.executable, "manage.py", "check", "--fail-level", "WARNING"],
            check=False,
            **_options(project),
        )

        assert tested.returncode == 1
        assert f"ERROR: {_BOUNDARY_TEST} (home.tests.test_boundaries." in tested.stderr
        assert "home/helpers.py:1: (next.W136) home imports shop.forms" in tested.stderr
        assert "(next.E171)" in tested.stderr
        assert (checked.returncode, checked.stderr) == (0, "")
