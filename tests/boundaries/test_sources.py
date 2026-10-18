import ast
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from next.boundaries import sources
from next.boundaries.graph import App
from next.boundaries.manager import boundaries_manager
from next.boundaries.sources import (
    SourceFile,
    absolute_module,
    call_name,
    class_file,
    class_node,
    dotted,
    imported_names,
    page_trees,
    posix_relative,
    string,
    within,
)
from tests.support import file_router_config_entry
from tests.support.boundaries import boundary_project, scanned


_LAYOUT = {
    "shop": {
        "models.py": "",
        "tests_helpers.py": "",
        "tests.py": "",
        "tests/test_models.py": "",
        "migrations/0001_initial.py": "",
        "migrations_tools.py": "",
        "testsuite/run.py": "",
        "pages/template.djx": "",
        "pages/list.html": "",
        "__pycache__/cached.py": "",
        "node_modules/tool.py": "",
        ".hidden/secret.py": "",
        "notes.txt": "",
        "orders/__init__.py": "",
        "orders/views.py": "",
        "pages/page.py": "",
        "_components/card/component.py": "",
        "widgets/badge/component.py": "",
    },
    "shop.billing": {"models.py": ""},
}

_COMPONENTS = [
    {
        "BACKEND": "next.components.FileComponentsBackend",
        "COMPONENTS_DIR": "_components",
    },
    "junk",
    {"BACKEND": "next.components.FileComponentsBackend", "COMPONENTS_DIR": ""},
    {"BACKEND": "next.components.FileComponentsBackend", "COMPONENTS_DIR": "widgets"},
]


def _files(scan) -> list[tuple[str, str, str, str, bool]]:
    project = scan.project
    return [
        (
            source.app.label,
            project.relative(source.path),
            source.module,
            source.package,
            source.tree,
        )
        for source in project.files
    ]


def _trees(root: Path, **overrides: object) -> list[str]:
    with boundary_project(root, _LAYOUT, **overrides):
        apps = boundaries_manager.scan().graph.apps
        return [
            path.relative_to(root.resolve()).as_posix() for path in page_trees(apps)
        ]


def _source(module: str, *, package: str | None = None) -> SourceFile:
    app = App("shop", "shop", Path("/p/shop"))
    held = module.rpartition(".")[0] if package is None else package
    return SourceFile(
        app, Path("/p/shop/x.py"), module, held, tree=False, packaged=True
    )


class TestLoadProject:
    """Each app owns its Python files, without tests, migrations or a nested app."""

    def test_the_files_and_the_trees(self, tmp_path) -> None:
        scan = scanned(tmp_path, _LAYOUT, framework={"COMPONENT_BACKENDS": _COMPONENTS})

        assert _files(scan) == [
            (
                "billing",
                "shop/billing/__init__.py",
                "shop.billing",
                "shop.billing",
                False,
            ),
            (
                "billing",
                "shop/billing/apps.py",
                "shop.billing.apps",
                "shop.billing",
                False,
            ),
            (
                "billing",
                "shop/billing/models.py",
                "shop.billing.models",
                "shop.billing",
                False,
            ),
            ("shop", "shop/__init__.py", "shop", "shop", False),
            ("shop", "shop/apps.py", "shop.apps", "shop", False),
            (
                "shop",
                "shop/migrations_tools.py",
                "shop.migrations_tools",
                "shop",
                False,
            ),
            ("shop", "shop/models.py", "shop.models", "shop", False),
            ("shop", "shop/tests_helpers.py", "shop.tests_helpers", "shop", False),
            (
                "shop",
                "shop/_components/card/component.py",
                "shop._components.card.component",
                "shop._components.card",
                True,
            ),
            ("shop", "shop/orders/__init__.py", "shop.orders", "shop.orders", False),
            ("shop", "shop/orders/views.py", "shop.orders.views", "shop.orders", False),
            ("shop", "shop/pages/page.py", "shop.pages.page", "shop.pages", True),
            (
                "shop",
                "shop/testsuite/run.py",
                "shop.testsuite.run",
                "shop.testsuite",
                False,
            ),
            (
                "shop",
                "shop/widgets/badge/component.py",
                "shop.widgets.badge.component",
                "shop.widgets.badge",
                True,
            ),
        ]
        assert [
            scan.project.relative(path) for _app, path in scan.project.templates
        ] == ["shop/pages/list.html", "shop/pages/template.djx"]
        assert [source.path.name for source in scan.project.files_of("billing")] == [
            "__init__.py",
            "apps.py",
            "models.py",
        ]

    def test_the_trees(self, tmp_path) -> None:
        framework = {"COMPONENT_BACKENDS": _COMPONENTS}

        assert _trees(tmp_path, framework=framework) == [
            "shop/_components",
            "shop/pages",
            "shop/widgets",
        ]

    def test_without_routers_only_component_folders_are_trees(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(sources, "get_router_manager", lambda: (None, []))

        assert _trees(tmp_path) == ["shop/_components"]

    def test_a_renamed_component_folder_is_the_only_one(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(sources, "get_router_manager", lambda: (None, []))
        renamed = [
            {
                "BACKEND": "next.components.FileComponentsBackend",
                "COMPONENTS_DIR": "widgets",
            }
        ]

        assert _trees(tmp_path, framework={"COMPONENT_BACKENDS": renamed}) == [
            "shop/widgets"
        ]

    def test_a_tree_outside_every_app_is_left_out(self, tmp_path) -> None:
        site = tmp_path / "site_pages"
        site.mkdir()
        entry = file_router_config_entry(app_dirs=True, dirs=[site])

        assert "site_pages" not in _trees(
            tmp_path, framework={"PAGE_BACKENDS": [entry]}
        )

    def test_only_regular_packages_are_packaged(self, tmp_path) -> None:
        project = scanned(tmp_path, _LAYOUT).project
        packaged = {
            project.relative(source.path) for source in project.files if source.packaged
        }

        assert "shop/orders/views.py" in packaged
        assert "shop/models.py" in packaged
        assert "shop/pages/page.py" not in packaged
        assert "shop/widgets/badge/component.py" not in packaged

    def test_in_tree_answers_only_for_a_tree_file(self, tmp_path) -> None:
        project = scanned(tmp_path, _LAYOUT).project

        assert project.in_tree("shop.pages.page") is True
        assert project.in_tree("shop.models") is False
        assert project.in_tree("crm.pages.page") is False


class TestSyntax:
    """A file is parsed once, and one that does not parse answers `None`."""

    def test_a_parsed_file_is_kept(self, tmp_path) -> None:
        project = scanned(tmp_path, {"shop": {"models.py": "x = 1\n"}}).project
        path = project.apps[0].path / "models.py"

        assert project.syntax(path) is project.syntax(path)
        assert isinstance(project.syntax(path), ast.Module)

    @pytest.mark.parametrize(
        "content",
        [b"def broken(:\n", b"name = '\xff'\n"],
        ids=["syntax_error", "not_utf8"],
    )
    def test_a_file_that_does_not_parse(self, tmp_path, content: bytes) -> None:
        project = scanned(tmp_path, {"shop": {}}).project
        path = project.apps[0].path / "broken.py"
        path.write_bytes(content)

        assert project.syntax(path) is None

    @pytest.mark.parametrize(
        "content",
        [b"\xef\xbb\xbfname = 1\n", b"# -*- coding: latin-1 -*-\nname = '\xe9'\n"],
        ids=["bom", "coding_cookie"],
    )
    def test_a_file_python_reads_parses(self, tmp_path, content: bytes) -> None:
        project = scanned(tmp_path, {"shop": {}}).project
        path = project.apps[0].path / "encoded.py"
        path.write_bytes(content)

        syntax = project.syntax(path)

        assert syntax is not None
        assert project.syntax_error(path) is None
        assert [type(node).__name__ for node in syntax.body][-1] == "Assign"

    def test_a_missing_file(self, tmp_path) -> None:
        project = scanned(tmp_path, {"shop": {}}).project

        assert project.syntax(tmp_path / "gone.py") is None

    def test_relative_keeps_a_path_outside_the_base_dir(self, tmp_path) -> None:
        project = scanned(tmp_path / "project", {"shop": {}}).project

        assert project.relative(Path("/elsewhere/x.py")) == "/elsewhere/x.py"


class TestImportedNames:
    """Each name an import binds stands for the dotted path it was imported from."""

    def test_every_import_form(self) -> None:
        syntax = ast.parse(
            "import os.path\n"
            "import crm.models as crm_models\n"
            "from crm.services import notify as tell, record\n"
            "from . import utils\n"
            "from .orders import Order\n"
            "from crm.models import *\n"
            "from ...... import far\n"
        )

        names = imported_names(syntax, _source("shop.views"))

        assert names == {
            "os": "os",
            "crm_models": "crm.models",
            "tell": "crm.services.notify",
            "record": "crm.services.record",
            "utils": "shop.utils",
            "Order": "shop.orders.Order",
        }

    @pytest.mark.parametrize(
        ("statement", "package", "expected"),
        [
            ("from crm import models", "shop", "crm"),
            ("from . import models", "shop.orders", "shop.orders"),
            ("from .. import models", "shop.orders", "shop"),
            ("from ..crm import models", "shop.orders", "shop.crm"),
            ("from ... import models", "shop.orders", None),
        ],
    )
    def test_absolute_module(
        self, statement: str, package: str, expected: str | None
    ) -> None:
        node = ast.parse(statement).body[0]
        assert isinstance(node, ast.ImportFrom)

        assert absolute_module(node, _source("x", package=package)) == expected


class TestExpressions:
    """Names, attributes, callees and string literals read from the syntax."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("signals.changed", "crm.signals.changed"),
            ("Local", "shop.views.Local"),
            ("make().changed", None),
            ("items[0]", None),
        ],
    )
    def test_dotted(self, source: str, expected: str | None) -> None:
        node = ast.parse(source, mode="eval").body

        assert dotted(node, {"signals": "crm.signals"}, "shop.views") == expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("receiver(x)", "receiver"),
            ("dispatch.receiver(x)", "receiver"),
            ("f()()", None),
        ],
    )
    def test_call_name(self, source: str, expected: str | None) -> None:
        node = ast.parse(source, mode="eval").body
        assert isinstance(node, ast.Call)

        assert call_name(node) == expected

    @pytest.mark.parametrize(
        ("source", "expected"), [("'crm'", "crm"), ("3", None), ("name", None)]
    )
    def test_string(self, source: str, expected: str | None) -> None:
        assert string(ast.parse(source, mode="eval").body) == expected

    def test_string_of_nothing(self) -> None:
        assert string(None) is None


class TestWithin:
    """A path is a named module of an app, or lies in its package of that name."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/p/shop/signals.py", True),
            ("/p/shop/signals/orders.py", True),
            ("/p/shop/signals_extra.py", False),
            ("/p/shop/orders/signals.py", False),
            ("/p/crm/signals.py", False),
        ],
    )
    def test_within(self, path: str, *, expected: bool) -> None:
        app = App("shop", "shop", Path("/p/shop"))

        assert within(app, Path(path), "signals") is expected


class TestClassFile:
    """The file of a class is the file of its module, when the module has one."""

    def test_a_module_without_a_file(self) -> None:
        cls = type("Ghost", (), {"__module__": "ghost_models"})

        with patch.dict("sys.modules", {"ghost_models": ModuleType("ghost_models")}):
            assert class_file(cls) is None

    def test_a_module_with_a_file(self) -> None:
        cls = type("Here", (), {"__module__": __name__})

        assert class_file(cls) == Path(__file__).resolve()


class TestClassNode:
    """A class is the last one of its name at the top of the module, as Python binds."""

    _SYNTAX = ast.parse(
        "class Config:\n    first = 1\n\n\nclass Config:\n    second = 2\n\n\n"
        "class Outer:\n    class Inner:\n        pass\n"
    )

    @pytest.mark.parametrize(
        ("name", "line"), [("Config", 5), ("Outer", 9), ("Inner", None)]
    )
    def test_the_class(self, name: str, line: int | None) -> None:
        node = class_node(self._SYNTAX, name)

        assert (None if node is None else node.lineno) == line

    def test_no_syntax_is_no_class(self) -> None:
        assert class_node(None, "Config") is None


class TestPosixRelative:
    """A path below `BASE_DIR` shows relative to it, any other path whole."""

    @pytest.mark.parametrize(
        ("path", "base_dir", "shown"),
        [
            (Path("/p/shop/apps.py"), Path("/p"), "shop/apps.py"),
            (Path("/elsewhere/apps.py"), Path("/p"), "/elsewhere/apps.py"),
            (Path("/p/shop/apps.py"), None, "/p/shop/apps.py"),
        ],
        ids=["below", "outside", "no_base_dir"],
    )
    def test_posix_relative(
        self, path: Path, base_dir: Path | None, shown: str
    ) -> None:
        assert posix_relative(path, base_dir) == shown
