from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

import pytest

from next.static.runtime import (
    CHUNK_STATIC_PATHS,
    DECLARED_CHUNK_KEYS,
    LISTED_CHUNK_KEYS,
    NEXT_JS_STATIC_PATH,
)


ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "next" / "client"
BUILD = CLIENT / "build.mjs"

pytestmark = pytest.mark.skipif(
    not (CLIENT / "next.ts").exists(), reason="needs the source checkout"
)


def _ts_strings(source: str, pattern: str) -> set[str]:
    """Return the string literals of the array literal `pattern` introduces."""
    found = re.search(pattern + r"\s*\[([^\]]*)\]", source)
    assert found is not None, pattern
    return set(re.findall(r'"([^"]+)"', found.group(1)))


def _static_bundles() -> set[str]:
    """Return the runtime and every chunk as paths under the static root."""
    return {NEXT_JS_STATIC_PATH, *CHUNK_STATIC_PATHS.values()}


def _bundle(key: str) -> str:
    """Return the name of the bundle that serves the chunk `key`."""
    return CHUNK_STATIC_PATHS[key].removeprefix("next/next.").removesuffix(".min.js")


def _entry_source(key: str) -> str:
    """Return the source of the build entry the bundle of `key` is built from."""
    entry = _build_entries()[f"next.{_bundle(key)}.min"]
    return (ROOT / entry).read_text(encoding="utf-8")


def _build_entries() -> dict[str, str]:
    """Return the `build:next` esbuild entries, output name to source file."""
    script = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert script["scripts"]["build:next"] == f"node {BUILD.relative_to(ROOT)}"
    source = BUILD.read_text(encoding="utf-8")
    assert 'outdir: "next/static/next"' in source
    return dict(re.findall(r'"(next(?:\.\w+)?\.min)": "(\S+\.ts)"', source))


class TestChunkLists:
    """Every list of the chunks names the same ones."""

    def test_module_keys_name_every_bundle(self) -> None:
        source = (CLIENT / "next.ts").read_text(encoding="utf-8")
        modules = _ts_strings(source, r"static #modules = Object\.fromEntries\(\s*")
        assert modules == {_bundle(key) for key in CHUNK_STATIC_PATHS}

    def test_chunk_keys_name_every_shipped_chunk(self) -> None:
        assert set(LISTED_CHUNK_KEYS) == {
            "scripts",
            "stream",
            "ws",
            "csrf",
            "poll",
            "dev",
            "forms",
            "layers",
        }
        assert {
            "boundaries",
            "reload",
            "more",
            "router",
            "intent",
        } == DECLARED_CHUNK_KEYS
        assert set(CHUNK_STATIC_PATHS) == {*LISTED_CHUNK_KEYS, *DECLARED_CHUNK_KEYS}

    def test_build_writes_every_static_bundle(self) -> None:
        built = {f"next/{name}.js" for name in _build_entries()}
        assert built == _static_bundles()

    def test_each_entry_extends_the_runtime_under_its_bundle_name(self) -> None:
        for key in CHUNK_STATIC_PATHS:
            name = _bundle(key)
            source = _entry_source(key)
            assert f'Next._extend(\n  "{name}"' in source or (
                f'Next._extend("{name}"' in source
            ), key
            assert "_land" not in source, key

    def test_each_chunk_entry_is_named_by_its_bundle(self) -> None:
        entries = _build_entries()
        for key, path in CHUNK_STATIC_PATHS.items():
            name = _bundle(key)
            assert path == f"next/next.{name}.min.js"
            assert entries[f"next.{name}.min"] == f"next/client/entries/{name}.ts"

    def test_build_hook_checks_every_static_bundle(self) -> None:
        tree = ast.parse((ROOT / "build_hooks.py").read_text(encoding="utf-8"))
        bundles = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_BUNDLES"
                for target in node.targets
            )
        )
        assert {f"next/{name}" for name in bundles} == _static_bundles()

    @pytest.mark.parametrize("target", ["wheel", "sdist"])
    def test_packaging_ships_every_static_bundle(self, target: str) -> None:
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        artifacts = config["tool"]["hatch"]["build"]["targets"][target]["artifacts"]
        assert {
            path.removeprefix("next/static/") for path in artifacts
        } == _static_bundles()
