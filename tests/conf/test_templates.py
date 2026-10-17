import os
import subprocess
import sys
from pathlib import Path

from django.core.management import call_command

import next.conf


_TEMPLATES = Path(next.conf.__file__).parent

_INSTALLED = '    "home",\n]'


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
    return {"cwd": project, "env": env, "capture_output": True, "text": True}


class TestProjectTemplates:
    """A project and an app made from the shipped templates start clean and strict."""

    def test_a_new_project_with_a_new_app(self, tmp_path) -> None:
        project = tmp_path / "boundsite"
        (project / "shop").mkdir(parents=True)
        project_template = _TEMPLATES / "project_template"
        app_template = _TEMPLATES / "app_template"
        call_command(
            "startproject", "boundsite", str(project), template=str(project_template)
        )
        call_command(
            "startapp", "shop", str(project / "shop"), template=str(app_template)
        )
        settings = project / "boundsite" / "settings.py"
        text = settings.read_text()
        settings.write_text(text.replace(_INSTALLED, '    "home",\n    "shop",\n]'))
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

        assert generated == (
            _expected(project_template, "", "boundsite")
            | _expected(app_template, "shop/")
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
