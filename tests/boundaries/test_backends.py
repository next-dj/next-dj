import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.checks import Error
from django.core.management import CommandError, call_command

from next.boundaries import imports as imports_module
from next.boundaries.backends import (
    AstImportGraph,
    GrimpImportGraph,
    ImportGraphBackend,
    import_graph,
    project_packages,
)
from next.boundaries.checks import check_boundaries
from next.boundaries.edges import collect_edges
from next.boundaries.errors import ImportGraphError, ImportScanError
from next.boundaries.graph import App
from next.boundaries.imports import Import, ImportScan
from next.boundaries.manager import boundaries_manager
from next.boundaries.rules import IMPORT_GRAPH
from next.boundaries.sources import Project
from tests.support import importable_dir
from tests.support.boundaries import UNREADABLE, Unreadable, boundary_project
from tests.support.cases.boundaries import IMPORT_FORMS


_ADMIN = Path(__file__).resolve().parents[2] / "examples" / "admin"

_GRAPH = {"BOUNDARIES_IMPORT_GRAPH": "next.boundaries.AstImportGraph"}
_MISSING_GRAPH = {"BOUNDARIES_IMPORT_GRAPH": "next.boundaries.nowhere.Graph"}


def _edges(project: Project, backend: ImportGraphBackend) -> set[tuple]:
    return {
        (edge.source, edge.target, project.relative(edge.file), edge.line, edge.detail)
        for edge in collect_edges(project, backend.scan(project))
        if edge.kind == "import"
    }


def _project(root: Path, *names: str) -> Project:
    apps = tuple(App(name, name, root / name) for name in names)
    return Project(root, apps, (), {}, ())


def _write(root: Path, files: dict[str, str | bytes | Unreadable]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, Unreadable):
            path.symlink_to(path.with_name("missing"))
        elif isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text)


def _both(root: Path, *names: str) -> tuple[ImportScan, ImportScan]:
    project = _project(root, *names)
    with importable_dir(root):
        return GrimpImportGraph().scan(project), AstImportGraph().scan(project)


class TestParity:
    """The ast reader finds the modules, imports and edges grimp finds."""

    def test_every_import_form(self, tmp_path) -> None:
        with boundary_project(tmp_path, IMPORT_FORMS):
            project = boundaries_manager.scan().project
            assert project is not None
            (tmp_path / "ledger" / "util.py").write_text("import ledger.core\n")
            grimp = GrimpImportGraph().scan(project)
            ast = AstImportGraph().scan(project)
            grimp_edges = _edges(project, GrimpImportGraph())
            ast_edges = _edges(project, AstImportGraph())

        assert ast.modules == grimp.modules
        assert set(ast.imports) == set(grimp.imports)
        assert ast.imports == tuple(sorted(ast.imports))
        assert ast_edges == grimp_edges
        assert {
            ("shop", "crm", "shop/views.py", 14, "crm.types"),
            ("shop", "crm", "shop/views.py", 16, "crm.lazy"),
            ("shop", "crm", "shop/views.py", 3, "crm.sub.deep"),
            ("shop", "crm", "shop/views.py", 5, "crm"),
            ("crm", "core", "crm/sub/deep.py", 2, "ledger.core.services"),
        } <= ast_edges
        assert not {row for row in ast_edges if row[2].startswith("shop/tests")}
        assert {
            (row[3], row[4])
            for row in ast_edges
            if row[2] == "shop/views.py" and row[3] >= 18
        } == {
            (21, "crm.sub.deep"),
            (22, "crm.pages.home.page"),
            (23, "crm.pages.home.page"),
            (24, "crm.extra.tool"),
        }

    def test_the_admin_example(self) -> None:
        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join([str(_ADMIN), str(_ADMIN.parent)]),
        }

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import django

django.setup()

from next.boundaries.backends import AstImportGraph, GrimpImportGraph
from next.boundaries.edges import collect_edges
from next.boundaries.graph import project_apps
from next.boundaries.sources import load_project
from next.utils import resolve_base_dir

base_dir = resolve_base_dir()
project = load_project(base_dir, project_apps(base_dir))
found = []
for backend in (GrimpImportGraph(), AstImportGraph()):
    scan = backend.scan(project)
    edges = {
        (edge.source, edge.target, str(edge.file), edge.line, edge.detail)
        for edge in collect_edges(project, scan)
        if edge.kind == "import"
    }
    found.append((scan.modules, frozenset(scan.imports), frozenset(edges)))
print(len(found[0][2]), found[0] == found[1])
""",
            ],
            cwd=_ADMIN,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.split() == ["3", "True"]


class TestAstModules:
    """A module is a file of a regular package, read the way grimp walks it."""

    def test_a_namespace_top_package(self, tmp_path) -> None:
        _write(
            tmp_path,
            {
                "ns/loose.py": "import ns.app.models\n",
                "ns/app/__init__.py": "",
                "ns/app/models.py": "from ns import loose\n",
                "ns/app/inner/deep.py": "",
                "ns/empty/README": "",
            },
        )

        with importable_dir(tmp_path):
            scan = AstImportGraph().scan(_project(tmp_path, "ns.app"))

        assert scan.modules == frozenset({"ns", "ns.loose", "ns.app", "ns.app.models"})
        assert scan.imports == (
            Import("ns.app.models", "ns.loose", 1),
            Import("ns.loose", "ns.app.models", 1),
        )

    def test_a_package_off_the_path(self, tmp_path) -> None:
        with pytest.raises(ImportScanError, match="nowhere is no package"):
            AstImportGraph().scan(_project(tmp_path, "nowhere"))

    @pytest.mark.parametrize(
        ("source", "line", "reason"),
        [
            ("x = 1\ndef broken(:\n", 2, "invalid syntax"),
            (b"x = 1\nname = '\xff'\n", 2, "'utf-8' codec can't decode byte 0xff"),
            (UNREADABLE, 0, "It cannot be read."),
            ("x = 1\nfrom ... import far\n", 2, "reaches above its package"),
        ],
        ids=["syntax", "not_utf8", "unreadable", "escaping"],
    )
    def test_a_module_it_cannot_read(self, tmp_path, source, line, reason) -> None:
        _write(tmp_path, {"pkg/__init__.py": "", "pkg/bad.py": source})

        with importable_dir(tmp_path), pytest.raises(ImportScanError) as raised:
            AstImportGraph().scan(_project(tmp_path, "pkg"))

        assert (raised.value.file, raised.value.line) == ("pkg/bad.py", line)
        assert reason in raised.value.reason

    def test_a_module_file_wins_over_a_package_beside_it(self, tmp_path) -> None:
        _write(
            tmp_path,
            {
                "pkg/__init__.py": "",
                "pkg/y.py": "",
                "pkg/dup.py": "from . import y\n",
                "pkg/dup/__init__.py": "from . import y\nimport pkg\n",
                "pkg/dup/y.py": "",
            },
        )

        grimp, ast = _both(tmp_path, "pkg")

        assert ast == grimp
        assert [item for item in ast.imports if item.importer == "pkg.dup"] == [
            Import("pkg.dup", "pkg.y", 1)
        ]

    def test_the_packages_of_a_project(self, tmp_path) -> None:
        project = _project(tmp_path, "shop", "ledger.core", "ledger.money")

        assert project_packages(project) == ("ledger", "shop")


class TestEncodings:
    """A source reads as Python reads it, and grimp names the file it cannot read."""

    def test_a_bom(self, tmp_path) -> None:
        _write(
            tmp_path, {"pkg/__init__.py": "", "pkg/m.py": b"\xef\xbb\xbfimport pkg\n"}
        )

        grimp, ast = _both(tmp_path, "pkg")

        assert ast == grimp
        assert ast.imports == (Import("pkg.m", "pkg", 1),)

    def test_a_coding_cookie(self, tmp_path) -> None:
        latin = b"# -*- coding: latin-1 -*-\nimport pkg\nname = '\xe9'\n"
        _write(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": latin})
        project = _project(tmp_path, "pkg")

        with importable_dir(tmp_path):
            ast = AstImportGraph().scan(project)
            with pytest.raises(ImportScanError) as raised:
                GrimpImportGraph().scan(project)

        assert ast.imports == (Import("pkg.m", "pkg", 2),)
        assert (raised.value.file, raised.value.line) == ("pkg/m.py", 0)
        assert raised.value.reason == (
            "grimp reads only UTF-8 sources, and this one is not. Save it as UTF-8, "
            "or name next.boundaries.AstImportGraph in BOUNDARIES_IMPORT_GRAPH."
        )

    def test_a_panic_over_utf8_sources_stays(self, tmp_path, monkeypatch) -> None:
        _write(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": "import pkg\n"})

        def panicking(packages):
            reason = f"{imports_module.PANIC}boom"
            raise ImportScanError(reason)

        monkeypatch.setattr(imports_module, "package_imports", panicking)

        with importable_dir(tmp_path), pytest.raises(ImportScanError) as raised:
            GrimpImportGraph().scan(_project(tmp_path, "pkg"))

        assert raised.value.reason == "grimp panicked: boom"


class TestChoice:
    """`BOUNDARIES_IMPORT_GRAPH` names the backend, and `None` picks grimp if it can."""

    @pytest.mark.parametrize(
        ("available", "expected"),
        [(True, GrimpImportGraph), (False, AstImportGraph)],
        ids=["grimp", "no_grimp"],
    )
    def test_unset(self, tmp_path, monkeypatch, available, expected) -> None:
        monkeypatch.setattr(imports_module, "AVAILABLE", available)

        with boundary_project(tmp_path, {}):
            assert type(import_graph()) is expected

    def test_a_named_backend(self, tmp_path) -> None:
        with boundary_project(tmp_path, {}, framework=_GRAPH):
            assert type(import_graph()) is AstImportGraph

    def test_grimp_named_without_grimp(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(imports_module, "AVAILABLE", False)
        framework = {"BOUNDARIES_IMPORT_GRAPH": "next.boundaries.GrimpImportGraph"}

        with (
            boundary_project(tmp_path, {}, framework=framework),
            pytest.raises(ImportGraphError) as raised,
        ):
            import_graph()

        assert str(raised.value) == (
            "NEXT_FRAMEWORK['BOUNDARIES_IMPORT_GRAPH'] names GrimpImportGraph, but "
            "grimp is not installed. Install the next.dj[boundaries] extra, or leave "
            "the key unset."
        )

    @pytest.mark.parametrize(
        ("dotted", "said"),
        [
            ("next.boundaries.nowhere.Graph", "could not be imported"),
            ("next.boundaries.App", "is not a next.boundaries.ImportGraphBackend"),
            ("next.boundaries.ImportGraphBackend", "is abstract"),
        ],
        ids=["missing", "not_a_backend", "abstract"],
    )
    def test_a_path_it_refuses(self, tmp_path, dotted, said) -> None:
        framework = {"BOUNDARIES_IMPORT_GRAPH": dotted}

        with (
            boundary_project(tmp_path, {}, framework=framework),
            pytest.raises(ImportGraphError, match="cannot be used") as raised,
        ):
            import_graph()

        assert said in str(raised.value)


class TestRefusedBackend:
    """A refused backend is the one E176, and the subcommand stops on it."""

    def test_the_check(self, tmp_path) -> None:
        apps = {"shop": {"views.py": "x = 1\n"}}

        with boundary_project(tmp_path, apps, framework=_MISSING_GRAPH):
            (message,) = check_boundaries()

        assert (type(message), message.id, message.obj) == (Error, IMPORT_GRAPH, None)
        assert message.msg.startswith(
            "NEXT_FRAMEWORK['BOUNDARIES_IMPORT_GRAPH'] cannot be used. "
        )

    @pytest.mark.parametrize("args", [(), ("shop",), ("--refreeze",)])
    def test_the_subcommand(self, tmp_path, args) -> None:
        apps = {"shop": {"views.py": "x = 1\n"}}

        with (
            boundary_project(tmp_path, apps, framework=_MISSING_GRAPH),
            pytest.raises(CommandError, match="BOUNDARIES_IMPORT_GRAPH"),
        ):
            call_command("next", "boundaries", *args)

    def test_the_ast_reader_checks_a_project(self, tmp_path) -> None:
        apps = {
            "shop": {"views.py": "from crm.utils import helper\n"},
            "crm": {"utils.py": "helper = 1\n"},
        }

        with boundary_project(tmp_path, apps, framework=_GRAPH):
            messages = check_boundaries()

        assert [message.id for message in messages] == ["next.W136"]
