import importlib
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import TypeAliasType

from tests.django_setup import PROJECT_ROOT


_EARLY_APP_MODULE = textwrap.dedent(
    """
    from django.apps import AppConfig

    from next.seo import RobotsRule
    from next.signals import page_rendered
    from next.static import default_kinds


    class EarlyConfig(AppConfig):
        name = "earlyapp"
    """
)


# The probe runs beside the xdist workers, so it takes a few cores rather than all.
_IMPORT_WORKERS = min(4, os.cpu_count() or 1)


def _next_module_names() -> list[str]:
    """Name every module of the imported package, the installed wheel under CI.

    The test matrix checks out no source tree, so the package itself is the list.
    """
    spec = importlib.util.find_spec("next")
    assert spec is not None
    assert spec.origin is not None
    package = Path(spec.origin).parent
    names = []
    for path in sorted(package.rglob("*.py")):
        parts = ("next", *path.relative_to(package).with_suffix("").parts)
        names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


def _failure(completed: subprocess.CompletedProcess[str]) -> str | None:
    return None if completed.returncode == 0 else completed.stderr


def _import_each_first(names: list[str]) -> dict[str, str]:
    """Import every name before any other framework module, answering the failures.

    Forking skips the interpreter start, and the preloaded parent holds no `next`.
    """
    # Importing a module first runs its packages first, so a module that a package
    # run already loaded repeats that run and gets no child of its own.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import gc, importlib, json, os, sys, traceback\n"
                "\n"
                "import django.db.models, django.forms, django.http, django.template.base\n"
                "import django.test, django.urls, django.views.generic\n"
                "from django.conf import settings\n"
                "\n"
                "from tests.django_setup import build_test_settings\n"
                "\n"
                "settings.configure(**build_test_settings())\n"
                'limit = int(os.environ["NEXT_IMPORT_WORKERS"])\n'
                "names = json.load(sys.stdin)\n"
                "\n"
                "\n"
                "def is_next(module):\n"
                '    return module == "next" or module.startswith("next.")\n'
                "\n"
                "\n"
                "def spawn(body):\n"
                "    read, write = os.pipe()\n"
                "    pid = os.fork()\n"
                "    if pid == 0:\n"
                "        os.close(read)\n"
                "        try:\n"
                "            message = body()\n"
                "        except BaseException:\n"
                "            os.write(write, traceback.format_exc().encode())\n"
                "            os._exit(1)\n"
                "        os.write(write, message.encode())\n"
                "        os._exit(0)\n"
                "    os.close(write)\n"
                "    return pid, read\n"
                "\n"
                "\n"
                "def survey():\n"
                "    before = set(sys.modules)\n"
                "    for name in names:\n"
                "        try:\n"
                "            importlib.import_module(name)\n"
                "        except BaseException:\n"
                "            pass\n"
                "    return json.dumps([m for m in set(sys.modules) - before if not is_next(m)])\n"
                "\n"
                "\n"
                "def first(name):\n"
                "    importlib.import_module(name)\n"
                "    return json.dumps([m for m in sys.modules if is_next(m)])\n"
                "\n"
                "\n"
                "def reap(running, failures, loaded):\n"
                "    pid, status = os.wait()\n"
                "    name, read = running.pop(pid)\n"
                "    with os.fdopen(read) as pipe:\n"
                "        message = pipe.read().strip()\n"
                "    if status:\n"
                "        failures[name] = message.splitlines()[-1] if message else str(status)\n"
                "    else:\n"
                "        loaded[name] = set(json.loads(message))\n"
                "\n"
                "\n"
                "def ancestors(name):\n"
                '    parts = name.split(".")\n'
                '    return [".".join(parts[:end]) for end in range(1, len(parts))]\n'
                "\n"
                "\n"
                "survey_pid, survey_read = spawn(survey)\n"
                "with os.fdopen(survey_read) as pipe:\n"
                "    outside = pipe.read()\n"
                "_pid, status = os.waitpid(survey_pid, 0)\n"
                "for module in json.loads(outside) if status == 0 else []:\n"
                "    try:\n"
                "        importlib.import_module(module)\n"
                "    except Exception:\n"
                "        pass\n"
                "preloaded = sorted(m for m in sys.modules if is_next(m))\n"
                "if preloaded:\n"
                '    raise SystemExit(f"preloading imported {preloaded}")\n'
                "gc.freeze()\n"
                "\n"
                "failures, loaded = {}, {}\n"
                'for depth in sorted({name.count(".") for name in names}):\n'
                "    running = {}\n"
                "    for name in names:\n"
                '        if name.count(".") != depth:\n'
                "            continue\n"
                "        if any(name in loaded.get(package, ()) for package in ancestors(name)):\n"
                "            continue\n"
                "        if len(running) >= limit:\n"
                "            reap(running, failures, loaded)\n"
                "        pid, read = spawn(lambda name=name: first(name))\n"
                "        running[pid] = (name, read)\n"
                "    while running:\n"
                "        reap(running, failures, loaded)\n"
                "print(json.dumps(failures))\n"
            ),
        ],
        cwd=PROJECT_ROOT,
        input=json.dumps(names),
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "NEXT_IMPORT_WORKERS": str(_IMPORT_WORKERS)},
    )
    return json.loads(completed.stdout)


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
        assert _import_each_first(_next_module_names()) == {}

    def test_the_probe_reports_a_module_that_fails_to_import(self) -> None:
        names = ["next.pages", "next.pages.manager", "next.nowhere"]
        assert _import_each_first(names) == {
            "next.nowhere": "ModuleNotFoundError: No module named 'next.nowhere'"
        }

    def test_an_app_listed_before_next_imports_its_areas(self, tmp_path: Path) -> None:
        package = tmp_path / "earlyapp"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "apps.py").write_text(_EARLY_APP_MODULE)
        assert _setup_with_early_app(tmp_path) is None


def _alias_values(names: list[str]) -> tuple[dict[str, object], dict[str, str]]:
    """Evaluate every `type` alias the modules define, splitting off the failures."""
    values: dict[str, object] = {}
    unresolved: dict[str, str] = {}
    for name in names:
        for attr, alias in vars(importlib.import_module(name)).items():
            if type(alias) is not TypeAliasType or alias.__module__ != name:
                continue
            try:
                values[f"{name}.{attr}"] = alias.__value__
            except NameError as exc:
                unresolved[f"{name}.{attr}"] = str(exc)
    return values, unresolved


class TestTypeAliases:
    """Every `type` alias evaluates at runtime, the way autodoc reads it."""

    def test_every_alias_value_resolves(self) -> None:
        values, unresolved = _alias_values(_next_module_names())
        assert unresolved == {}
        assert "next.seo.registry.KwargsOf" in values
