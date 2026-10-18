import shutil
import sys
from pathlib import Path

import pytest
from django.apps import AppConfig, apps as django_apps, config as app_config_module

from next.boundaries.errors import SyncError, UnknownAppError
from next.boundaries.graph import Graph
from next.boundaries.manager import Scan, boundaries_manager
from next.boundaries.sources import Project
from next.boundaries.sync import Change, Plan, apply, plan
from tests.support.boundaries import (
    UNREADABLE,
    app_config,
    boundary_project,
    write_boundary_apps,
)
from tests.support.cases.boundaries import UNREADABLE_DEPENDS_ON, UnreadableDependsOn
from tests.support.mounts import installed_apps


_HEAD = "from django.apps import AppConfig\n\n\n"

_BASE = (
    'from django.apps import AppConfig\n\n\nclass Base(AppConfig):\n    name = "shop"\n'
)

_CRM = {"services.py": "def notify():\n    pass\n"}
_BILLING = {"services.py": "def pay():\n    pass\n"}
_USES_CRM = "from crm.services import notify\n"
_USES_BOTH = "from crm.services import notify\nfrom billing.services import pay\n"


def _project(shop: str, views: str = _USES_CRM, **files: str) -> dict:
    return {
        "shop": {"apps.py": shop, "views.py": views, **files},
        "crm": {**_CRM, "apps.py": app_config("crm", depends_on=())},
        "billing": {**_BILLING, "apps.py": app_config("billing", depends_on=())},
    }


def _synced(root: Path, apps: dict, key: str | None = None) -> dict[str, str]:
    with boundary_project(root, apps):
        project = boundaries_manager.scan().project
        assert project is not None
        changes = plan(boundaries_manager.scan(), key).changes
        assert all(change.path.is_relative_to(root.resolve()) for change in changes)
        apply(changes, project)
        boundaries_manager.reset()
        again = plan(boundaries_manager.scan(), key).changes
    assert again == ()
    return {project.relative(change.path): change.after for change in changes}


def _project_at(root: Path) -> Project:
    return Project(root, (), (), {}, ())


class TestInsert:
    """A class without `depends_on` gets one after its settings, naming every app."""

    def test_after_the_name(self, tmp_path) -> None:
        apps = {
            "shop": {"apps.py": app_config("shop"), "views.py": _USES_CRM},
            "crm": {**_CRM, "apps.py": app_config("crm")},
        }

        assert _synced(tmp_path, apps) == {
            "crm/apps.py": (
                f"{_HEAD}class Config(AppConfig):\n"
                '    name = "crm"\n'
                "    depends_on = ()\n"
            ),
            "shop/apps.py": (
                f"{_HEAD}class Config(AppConfig):\n"
                '    name = "shop"\n'
                '    depends_on = ("crm",)\n'
            ),
        }

    def test_after_the_last_setting(self, tmp_path) -> None:
        shop = (
            f"{_HEAD}class Config(AppConfig):\n"
            '    name = "shop"\n'
            '    verbose_name: str = "Shop"\n\n'
            "    def ready(self):\n"
            "        pass\n"
        )

        synced = _synced(tmp_path, _project(shop, _USES_BOTH))

        assert synced == {
            "shop/apps.py": (
                f"{_HEAD}class Config(AppConfig):\n"
                '    name = "shop"\n'
                '    verbose_name: str = "Shop"\n'
                '    depends_on = ("billing", "crm")\n\n'
                "    def ready(self):\n"
                "        pass\n"
            )
        }

    def test_after_the_docstring(self, tmp_path) -> None:
        shop = (
            "import shop.base\n\n\n"
            "class Config(shop.base.Base):\n"
            '    """The shop."""\n\n'
            "    def ready(self):\n"
            "        pass\n"
        )

        synced = _synced(tmp_path, _project(shop, **{"base.py": _BASE}))

        assert synced == {
            "shop/apps.py": (
                "import shop.base\n\n\n"
                "class Config(shop.base.Base):\n"
                '    """The shop."""\n\n'
                '    depends_on = ("crm",)\n\n'
                "    def ready(self):\n"
                "        pass\n"
            )
        }

    def test_after_a_docstring_with_no_blank_line(self, tmp_path) -> None:
        shop = (
            'import shop.base\n\n\nclass Config(shop.base.Base):\n    """The shop."""'
        )

        synced = _synced(tmp_path, _project(shop, **{"base.py": _BASE}))

        assert synced == {
            "shop/apps.py": (
                "import shop.base\n\n\n"
                "class Config(shop.base.Base):\n"
                '    """The shop."""\n\n'
                '    depends_on = ("crm",)\n'
            )
        }

    def test_into_an_empty_body(self, tmp_path) -> None:
        shop = "import shop.base\n\n\nclass Config(shop.base.Base):\n    pass\n"

        synced = _synced(tmp_path, _project(shop, **{"base.py": _BASE}))

        assert synced == {
            "shop/apps.py": (
                "import shop.base\n\n\n"
                "class Config(shop.base.Base):\n"
                '    depends_on = ("crm",)\n'
            )
        }

    def test_into_an_ellipsis_body(self, tmp_path) -> None:
        shop = "import shop.base\n\n\nclass Config(shop.base.Base):\n    ...\n"

        synced = _synced(tmp_path, _project(shop, **{"base.py": _BASE}))

        assert synced == {
            "shop/apps.py": (
                "import shop.base\n\n\n"
                "class Config(shop.base.Base):\n"
                '    depends_on = ("crm",)\n'
            )
        }

    def test_a_file_without_a_last_newline(self, tmp_path) -> None:
        shop = f'{_HEAD}class Config(AppConfig):\n    name = "shop"'

        assert _synced(tmp_path, _project(shop)) == {
            "shop/apps.py": (
                f"{_HEAD}class Config(AppConfig):\n"
                '    name = "shop"\n'
                '    depends_on = ("crm",)\n'
            )
        }


class TestRewrite:
    """An existing literal tuple keeps its names, its order and what surrounds it."""

    def test_names_are_appended(self, tmp_path) -> None:
        shop = (
            f"{_HEAD}class Config(AppConfig):\n"
            '    name = "shop"\n'
            '    depends_on: tuple[str, ...] = ("django.contrib.auth",)  # keep\n'
        )

        assert _synced(tmp_path, _project(shop, _USES_BOTH)) == {
            "shop/apps.py": (
                f"{_HEAD}class Config(AppConfig):\n"
                '    name = "shop"\n'
                '    depends_on: tuple[str, ...] = ("django.contrib.auth", "billing", '
                '"crm")  # keep\n'
            )
        }

    def test_a_long_tuple_is_laid_out_one_name_a_line(self, tmp_path) -> None:
        shop = (
            f"{_HEAD}class Config(AppConfig):\n"
            '    name = "shop"\n'
            "    depends_on: tuple[str, ...] = (\n"
            '        "django.contrib.contenttypes",\n'
            '        "django.contrib.auth",\n'
            "    )\n"
            '    label = "shop"\n'
        )

        assert _synced(tmp_path, _project(shop, _USES_BOTH)) == {
            "shop/apps.py": (
                f"{_HEAD}class Config(AppConfig):\n"
                '    name = "shop"\n'
                "    depends_on: tuple[str, ...] = (\n"
                '        "django.contrib.contenttypes",\n'
                '        "django.contrib.auth",\n'
                '        "billing",\n'
                '        "crm",\n'
                "    )\n"
                '    label = "shop"\n'
            )
        }

    @pytest.mark.parametrize(
        ("size", "folded"), [(60, True), (61, False)], ids=["88", "89"]
    )
    def test_a_line_of_88_stays_one_line(self, tmp_path, size, folded) -> None:
        wide = "w" * size
        shop = app_config("shop", depends_on=(wide,))
        flat = f'    depends_on = ("{wide}", "crm")\n'
        open_ = f'    depends_on = (\n        "{wide}",\n        "crm",\n    )\n'

        (text,) = _synced(tmp_path, _project(shop)).values()

        assert len(flat) - 1 == 28 + size
        assert text.endswith(flat if folded else open_)

    def test_an_open_tuple_folds_to_one_line(self, tmp_path) -> None:
        shop = (
            f"{_HEAD}class Config(AppConfig):\n"
            '    name = "shop"\n'
            "    depends_on = (\n"
            '        "billing",\n'
            "    )\n"
        )

        assert _synced(tmp_path, _project(shop, _USES_BOTH)) == {
            "shop/apps.py": (
                f"{_HEAD}class Config(AppConfig):\n"
                '    name = "shop"\n'
                '    depends_on = ("billing", "crm")\n'
            )
        }

    def test_nothing_missing_is_no_change(self, tmp_path) -> None:
        shop = app_config("shop", depends_on=("crm", "django.contrib.auth"))

        with boundary_project(tmp_path, _project(shop)):
            assert plan(boundaries_manager.scan()) == Plan((), ())

    def test_a_name_with_wide_characters_before_it(self, tmp_path) -> None:
        shop = (
            "from typing import Annotated\n\n"
            f"{_HEAD}class Config(AppConfig):\n"
            '    name = "shop"\n'
            '    depends_on: Annotated[tuple[str, ...], "магазин"] = ()\n'
        )

        synced = _synced(tmp_path, _project(shop))

        assert synced["shop/apps.py"].endswith(
            '    depends_on: Annotated[tuple[str, ...], "магазин"] = ("crm",)\n'
        )


def _two_configs(root: Path) -> None:
    write_boundary_apps(
        root,
        {
            "alpha": {
                "apps.py": (
                    f"{_HEAD}class AlphaConfig(AppConfig):\n"
                    '    name = "alpha"\n\n\n'
                    "class BetaConfig(AppConfig):\n"
                    '    name = "beta"\n'
                ),
                "views.py": "from beta.services import f\n",
            },
            "beta": {"services.py": "f = 1\nfrom gamma.services import g\n"},
            "gamma": {
                "services.py": "g = 1\n",
                "apps.py": app_config("gamma", depends_on=()),
            },
        },
    )


class TestTwoConfigsInOneFile:
    """Two classes of one file are both rewritten in one change."""

    def test_both(self, tmp_path) -> None:
        _two_configs(tmp_path)
        names = ("alpha.apps.AlphaConfig", "alpha.apps.BetaConfig", "gamma")

        with installed_apps(tmp_path, *names, BASE_DIR=tmp_path):
            (change,) = plan(boundaries_manager.scan()).changes

        assert change.path == (tmp_path / "alpha" / "apps.py").resolve()
        assert change.after == (
            f"{_HEAD}class AlphaConfig(AppConfig):\n"
            '    name = "alpha"\n'
            '    depends_on = ("beta",)\n\n\n'
            "class BetaConfig(AppConfig):\n"
            '    name = "beta"\n'
            '    depends_on = ("gamma",)\n'
        )


class TestOneApp:
    """A key picks one app by label or by name, and an unknown key is refused."""

    @pytest.mark.parametrize("key", ["shop", "proj.shop"])
    def test_by_label_or_name(self, tmp_path, key) -> None:
        apps = {
            "proj.shop": {"views.py": "from proj.crm.services import notify\n"},
            "proj.crm": _CRM,
        }

        with boundary_project(tmp_path, apps):
            (change,) = plan(boundaries_manager.scan(), key).changes

        assert change.after.endswith('    depends_on = ("proj.crm",)\n')

    def test_an_unknown_key(self, tmp_path) -> None:
        with (
            boundary_project(tmp_path, _project(app_config("shop"))),
            pytest.raises(UnknownAppError, match="'nope' is no application"),
        ):
            plan(boundaries_manager.scan(), "nope")

    def test_without_a_project(self) -> None:
        assert plan(Scan(None, Graph((), ()), (), ())) == Plan((), ())


_LITERAL = "depends_on is no literal tuple of strings."


class TestRefused:
    """What sync cannot rewrite names its file and line and writes no file at all."""

    @pytest.mark.parametrize("case", UNREADABLE_DEPENDS_ON, ids=lambda case: case.id)
    def test_a_value_it_cannot_read(self, tmp_path, case: UnreadableDependsOn) -> None:
        line = case.line
        with (
            boundary_project(tmp_path, _project(case.source)),
            pytest.raises(SyncError) as raised,
        ):
            plan(boundaries_manager.scan())

        assert str(raised.value) == (
            f"shop/apps.py, line {line}: {_LITERAL} Edit depends_on there by hand."
        )
        assert (raised.value.file, raised.value.line, raised.value.reason) == (
            "shop/apps.py",
            line,
            _LITERAL,
        )

    def test_an_inherited_value(self, tmp_path) -> None:
        base = _BASE + "    depends_on = ('crm',)\n"
        shop = "import shop.base\n\n\nclass Config(shop.base.Base):\n    pass\n"

        with (
            boundary_project(tmp_path, _project(shop, **{"base.py": base})),
            pytest.raises(SyncError) as raised,
        ):
            plan(boundaries_manager.scan())

        assert str(raised.value) == (
            "shop/apps.py, line 4: The class Config inherits depends_on. Edit "
            "depends_on there by hand."
        )

    def test_a_body_on_the_line_of_the_class(self, tmp_path) -> None:
        shop = f"{_HEAD}class Config(AppConfig): name = 'shop'\n"

        with (
            boundary_project(tmp_path, _project(shop)),
            pytest.raises(SyncError, match=r"line 4: The class Config keeps its body"),
        ):
            plan(boundaries_manager.scan())

    def test_a_class_below_the_top_level(self, tmp_path) -> None:
        shop = (
            f"{_HEAD}def build():\n"
            "    class Config(AppConfig):\n"
            "        name = 'shop'\n\n"
            "    return Config\n\n\n"
            "Config = build()\n"
        )

        with (
            boundary_project(tmp_path, _project(shop)),
            pytest.raises(SyncError) as raised,
        ):
            plan(boundaries_manager.scan())

        assert str(raised.value) == (
            "shop/apps.py: The module defines no class Config at its top level. Edit "
            "depends_on there by hand."
        )

    def test_a_file_that_does_not_parse(self, tmp_path, monkeypatch) -> None:
        apps = _project(app_config("shop"))
        apps["shop"]["lib/broken.py"] = UNREADABLE

        with boundary_project(tmp_path, apps):
            broken = tmp_path.resolve() / "shop" / "lib" / "broken.py"
            monkeypatch.setattr(sys.modules["shop.apps"], "__file__", str(broken))
            with pytest.raises(SyncError) as raised:
                plan(boundaries_manager.scan())

        assert raised.value.file == "shop/lib/broken.py.missing"
        assert raised.value.reason == "The AppConfig of shop cannot be read."


class TestNoOwnConfig:
    """An app without an AppConfig class of its own in the project has no file to edit."""

    def test_a_module_without_a_file_is_skipped(self, tmp_path, monkeypatch) -> None:
        with boundary_project(tmp_path, _project(app_config("shop"))):
            monkeypatch.delattr(sys.modules["shop.apps"], "__file__")
            synced = plan(boundaries_manager.scan())

        assert synced == Plan((), ("shop",))

    def test_django_s_own_class_is_skipped(self, tmp_path) -> None:
        base = _BASE.replace("    name", "    default = False\n    name")
        shop = "from shop.base import Base\n\n\nclass Config(Base):\n    pass\n"
        apps = _project(shop, **{"base.py": base})
        apps["crm"]["apps.py"] = app_config("crm")

        with boundary_project(tmp_path, apps):
            assert type(django_apps.get_app_config("shop")) is AppConfig
            synced = plan(boundaries_manager.scan())

        assert synced.skipped == ("shop",)
        assert [change.path.parent.name for change in synced.changes] == ["crm"]

    def test_by_key_it_is_refused_without_a_file_to_edit(self, tmp_path) -> None:
        base = _BASE.replace("    name", "    default = False\n    name")
        shop = "from shop.base import Base\n\n\nclass Config(Base):\n    pass\n"
        django_config = Path(app_config_module.__file__).resolve()
        before = django_config.read_bytes()

        with (
            boundary_project(tmp_path, _project(shop, **{"base.py": base})),
            pytest.raises(SyncError) as raised,
        ):
            plan(boundaries_manager.scan(), "shop")

        assert str(raised.value) == (
            "shop: shop has no AppConfig class of its own in the project. Give it an "
            "apps.py with an AppConfig subclass, then sync again."
        )
        assert django_config.read_bytes() == before

    def test_one_refusal_writes_nothing(self, tmp_path) -> None:
        apps = _project(app_config("shop"))
        apps["crm"]["apps.py"] = app_config("crm", "depends_on = ('a',) + ()")
        apps["crm"]["views.py"] = "from billing.services import pay\n"

        with boundary_project(tmp_path, apps), pytest.raises(SyncError):
            plan(boundaries_manager.scan())

        assert (tmp_path / "shop" / "apps.py").read_text() == app_config("shop")


class TestChange:
    """A change shows as a unified diff and writes its new text."""

    def test_the_diff(self, tmp_path) -> None:
        with boundary_project(tmp_path, _project(app_config("shop"))):
            scan = boundaries_manager.scan()
            (change,) = plan(scan).changes
            assert scan.project is not None
            diff = change.diff(scan.project)

        assert diff == (
            "--- a/shop/apps.py\n"
            "+++ b/shop/apps.py\n"
            "@@ -3,3 +3,4 @@\n"
            " \n"
            " class Config(AppConfig):\n"
            '     name = "shop"\n'
            '+    depends_on = ("crm",)\n'
        )

    def test_apply_writes_every_file(self, tmp_path) -> None:
        first, second = tmp_path / "a.py", tmp_path / "b.py"

        apply(
            (Change(first, "", "a = 1\n"), Change(second, "", "b = 2\n")),
            _project_at(tmp_path),
        )

        assert (first.read_text(), second.read_text()) == ("a = 1\n", "b = 2\n")

    def test_apply_names_a_file_it_cannot_write(self, tmp_path) -> None:
        target = tmp_path / "shop"
        target.mkdir()

        with pytest.raises(SyncError) as raised:
            apply((Change(target, "", "x = 1\n"),), _project_at(tmp_path))

        assert raised.value.file == "shop"
        assert raised.value.line == 0
        assert str(raised.value).startswith("shop: ")
        assert "files before it" not in str(raised.value)
        assert sorted(path.name for path in tmp_path.iterdir()) == ["shop"]

    def test_a_failed_replace_names_what_it_already_wrote(self, tmp_path) -> None:
        first = tmp_path / "a.py"
        first.write_text("a = 1\n")
        second = tmp_path / "shop"
        second.mkdir()

        with pytest.raises(SyncError) as raised:
            apply(
                (Change(first, "", "a = 2\n"), Change(second, "", "x = 1\n")),
                _project_at(tmp_path),
            )

        assert raised.value.file == "shop"
        assert str(raised.value).endswith(
            ". Files already written before it: 1. Edit depends_on there by hand."
        )
        assert first.read_text() == "a = 2\n"
        assert sorted(path.name for path in tmp_path.iterdir()) == ["a.py", "shop"]

    def test_apply_keeps_the_mode_of_a_file(self, tmp_path) -> None:
        path = tmp_path / "apps.py"
        path.write_text("a = 1\n")
        path.chmod(0o640)

        apply((Change(path, "a = 1\n", "a = 2\n"),), _project_at(tmp_path))

        assert (path.read_text(), path.stat().st_mode & 0o777) == ("a = 2\n", 0o640)

    def test_a_failed_stage_leaves_every_file(self, tmp_path) -> None:
        first = tmp_path / "a.py"
        first.write_text("a = 1\n")
        missing = tmp_path / "missing" / "b.py"

        with pytest.raises(SyncError) as raised:
            apply(
                (Change(first, "", "a = 2\n"), Change(missing, "", "b = 2\n")),
                _project_at(tmp_path),
            )

        assert raised.value.file == "missing/b.py"
        assert first.read_text() == "a = 1\n"
        assert sorted(path.name for path in tmp_path.iterdir()) == ["a.py"]

    def test_a_failed_copy_of_the_mode_removes_its_stage(
        self, tmp_path, monkeypatch
    ) -> None:
        path = tmp_path / "a.py"
        path.write_text("a = 1\n")

        def refuse(source, target) -> None:
            raise PermissionError(target)

        monkeypatch.setattr(shutil, "copymode", refuse)

        with pytest.raises(SyncError):
            apply((Change(path, "", "a = 2\n"),), _project_at(tmp_path))

        assert sorted(item.name for item in tmp_path.iterdir()) == ["a.py"]
        assert path.read_text() == "a = 1\n"
