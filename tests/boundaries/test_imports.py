import importlib.util
import sys
from types import SimpleNamespace

import pytest

from next.boundaries import imports
from next.boundaries.errors import BoundariesUnavailableError, ImportScanError
from next.boundaries.imports import Import, ImportScan, package_imports
from tests.support import importable_dir


class _SilentGraph:
    """A grimp graph whose one import carries no line details."""

    modules = frozenset({"pkg", "pkg.a", "pkg.b"})

    def find_modules_directly_imported_by(self, module: str) -> set[str]:
        return {"pkg.b"} if module == "pkg.a" else set()

    def get_import_details(self, *, importer: str, imported: str) -> list[dict]:
        return []


def _package(root, files: dict[str, str]) -> None:
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name, text in files.items():
        (package / name).write_text(text)


class TestPackageImports:
    """grimp reads the imports between the modules of the named packages."""

    def test_every_import_with_its_line(self, tmp_path) -> None:
        _package(tmp_path, {"a.py": "import pkg.b\n\nfrom pkg import b\n", "b.py": ""})

        with importable_dir(tmp_path):
            scan = package_imports(["pkg", "pkg"])

        assert scan == ImportScan(
            frozenset({"pkg", "pkg.a", "pkg.b"}),
            (Import("pkg.a", "pkg.b", 1), Import("pkg.a", "pkg.b", 3)),
        )

    def test_no_package_is_an_empty_scan(self) -> None:
        assert package_imports([]) == ImportScan(frozenset(), ())

    def test_an_import_without_details_is_on_line_zero(self, monkeypatch) -> None:
        stub = SimpleNamespace(build_graph=lambda *_names, **_options: _SilentGraph())
        monkeypatch.setattr(imports, "grimp", stub)

        assert package_imports(["pkg"]).imports == (Import("pkg.a", "pkg.b", 0),)

    def test_a_source_grimp_cannot_read_stops_the_scan(self, tmp_path) -> None:
        _package(tmp_path, {"a.py": "def broken(:\n"})

        with importable_dir(tmp_path), pytest.raises(ImportScanError) as raised:
            package_imports(["pkg"])

        assert raised.value.reason
        assert str(raised.value).startswith("The import graph of the project cannot")

    def test_a_grimp_panic_stops_the_scan(self, tmp_path) -> None:
        _package(tmp_path, {"a.py": "from ...... import far\n"})

        with importable_dir(tmp_path), pytest.raises(ImportScanError) as raised:
            package_imports(["pkg"])

        assert raised.value.reason.startswith("grimp panicked: ")

    def test_only_a_panic_of_the_rust_runtime_is_caught(self, monkeypatch) -> None:
        panic = type("PanicException", (BaseException,), {"__module__": "pyo3_runtime"})
        lookalike = type("PanicException", (BaseException,), {"__module__": "other"})

        message = "boom"

        def panicking(*names, cache_dir):
            raise panic(message)

        def pretending(*names, cache_dir):
            raise lookalike(message)

        monkeypatch.setattr(imports, "grimp", SimpleNamespace(build_graph=panicking))
        with pytest.raises(ImportScanError) as raised:
            package_imports(["pkg"])
        assert raised.value.reason == "grimp panicked: boom"

        monkeypatch.setattr(imports, "grimp", SimpleNamespace(build_graph=pretending))
        with pytest.raises(lookalike):
            package_imports(["pkg"])

    def test_any_other_base_exception_passes_through(self, monkeypatch) -> None:
        def interrupted(*names, cache_dir):
            raise KeyboardInterrupt

        monkeypatch.setattr(imports, "grimp", SimpleNamespace(build_graph=interrupted))

        with pytest.raises(KeyboardInterrupt):
            package_imports(["pkg"])

    def test_without_grimp_the_scan_is_unavailable(self, monkeypatch) -> None:
        monkeypatch.setattr(imports, "AVAILABLE", False)

        with pytest.raises(
            BoundariesUnavailableError, match="next\\.dj\\[boundaries\\]"
        ):
            package_imports(["pkg"])


class TestOptionalGrimp:
    """Without grimp the module still loads, with the boundaries switched off."""

    def test_the_module_loads_without_grimp(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "grimp", None)
        spec = importlib.util.spec_from_file_location(
            imports.__name__, imports.__file__
        )
        assert spec is not None
        assert spec.loader is not None
        fresh = importlib.util.module_from_spec(spec)

        spec.loader.exec_module(fresh)

        assert fresh.AVAILABLE is False
        with pytest.raises(BoundariesUnavailableError):
            fresh.package_imports(["pkg"])
