import ast
import runpy
import sys
from pathlib import Path

import pytest

from next import wire
from next.partial import headers, keys
from next.partial.stream import PROTOCOL
from next.static import protocol
from next.static.protocol import EXPORTS, ROOT, TARGET, main, render, values


_SOURCE = pytest.mark.skipif(
    not (ROOT / TARGET).exists(), reason="needs the source checkout"
)


class TestGeneratedModule:
    """The client reads the wire protocol from a module generated off the Python one."""

    @_SOURCE
    def test_the_checked_in_module_is_the_generated_one(self) -> None:
        written = (ROOT / TARGET).read_text(encoding="utf-8")
        assert written == render(), (
            f"{TARGET} is out of date with the Python constants of the protocol, "
            "run make build-js to write it again"
        )

    def test_each_export_names_its_python_value(self) -> None:
        found = values()
        assert found["HEADER_ZONE"] == headers.ZONE
        assert found["STREAM_ACCEPT"] == headers.STREAM_ACCEPT
        assert found["PREFETCH_SHELL"] == headers.PREFETCH_SHELL
        assert found["PROTOCOL"] == PROTOCOL
        assert found["CHUNK_TEMPLATE"] == keys.CHUNK_TEMPLATE
        assert found["CHUNK_END"] == keys.CHUNK_END
        assert found["ATTR_ACTION"] == wire.ACTION_ATTR
        assert found["ATTR_CONSENTED"] == keys.CONSENTED_ATTR
        assert found["REFUSAL_HTTP_ONLY"] == "http_only"
        assert list(found) == [name for name, _source, _constant in EXPORTS]

    def test_a_long_value_moves_past_the_width_as_prettier_puts_it(self) -> None:
        text = render()
        assert all(len(line) <= 88 for line in text.splitlines() if " = " in line)
        assert 'export const ROUTER_OFF =\n  "[data-next-router=off],' in text

    def test_the_script_runs_on_the_standard_library(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        script = ROOT / "next" / "static" / "protocol.py"
        tree = ast.parse(script.read_text(encoding="utf-8"))
        imported = {
            name.split(".")[0]
            for node in ast.walk(tree)
            for name in (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
        }
        assert imported <= set(sys.stdlib_module_names)
        written = tmp_path / "protocol.gen.ts"
        monkeypatch.setattr(sys, "argv", [str(script), str(written)])
        with pytest.raises(SystemExit) as done:
            runpy.run_path(str(script), run_name="__main__")
        assert done.value.code == 0
        assert written.read_text(encoding="utf-8") == render()


class TestMain:
    """The script writes the module when it changed and reports a stale one."""

    @pytest.fixture()
    def target(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        written = tmp_path / "protocol.gen.ts"
        monkeypatch.setattr(protocol, "TARGET", str(written))
        return written

    def test_it_writes_a_missing_module(self, target: Path) -> None:
        assert main([]) == 0
        assert target.read_text(encoding="utf-8") == render()

    def test_it_leaves_a_current_module_untouched(self, target: Path) -> None:
        target.write_text(render(), encoding="utf-8")
        stamp = target.stat().st_mtime_ns
        assert main([]) == 0
        assert target.stat().st_mtime_ns == stamp

    def test_a_named_path_stands_in_for_the_target(self, tmp_path: Path) -> None:
        written = tmp_path / "elsewhere.ts"
        assert main([str(written)]) == 0
        assert main(["--check", str(written)]) == 0

    def test_the_check_reports_a_stale_or_missing_module(self, target: Path) -> None:
        assert main(["--check"]) == 1
        target.write_text("stale", encoding="utf-8")
        assert main(["--check"]) == 1
        target.write_text(render(), encoding="utf-8")
        assert main(["--check"]) == 0


class TestReader:
    """The constants resolve through imports and through f-strings of other constants."""

    @pytest.fixture()
    def tree(self, tmp_path: Path) -> Path:
        package = tmp_path / "next" / "pkg"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("HOME = 'home'\n", encoding="utf-8")
        (package / "base.py").write_text(
            "NAME = 'data-x'\n\nclass Mode:\n    ON = 'on'\n", encoding="utf-8"
        )
        (package / "use.py").write_text(
            "from typing import Final\n"
            "from next.pkg import HOME\n"
            "from .base import NAME as ATTR\n"
            "from . import HOME as AGAIN\n"
            "SELECTOR: Final = f'[{ATTR}={HOME}|{AGAIN}]'\n"
            "SUM = 1 + 2\n"
            "ATTRIBUTE = f'{ATTR.upper}'\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_it_follows_absolute_and_relative_imports_to_a_module_or_package(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            protocol,
            "EXPORTS",
            (
                ("SELECTOR", "next/pkg/use.py", "SELECTOR"),
                ("ATTR", "next/pkg/use.py", "ATTR"),
                ("ON", "next/pkg/base.py", "Mode.ON"),
            ),
        )
        assert values(tree) == {
            "SELECTOR": "[data-x=home|home]",
            "ATTR": "data-x",
            "ON": "on",
        }

    @pytest.mark.parametrize("name", ["SUM", "ATTRIBUTE"])
    def test_it_refuses_what_is_no_constant(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        monkeypatch.setattr(protocol, "EXPORTS", (("X", "next/pkg/use.py", name),))
        with pytest.raises(ValueError, match="is not a constant the protocol reads"):
            values(tree)
