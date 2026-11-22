from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

import pytest

from next.static.runtime import (
    CHUNK_PAYLOAD_KEYS,
    CHUNK_STATIC_PATHS,
    DECLARED_CHUNK_KEYS,
    LISTED_CHUNK_KEYS,
    NEXT_JS_STATIC_PATH,
)


ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "next" / "client"

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


def _entry_source(key: str) -> str:
    """Return the source of the build entry the bundle `key` is built from."""
    entry = _build_entries()[f"next.{key}.min"]
    return (ROOT / entry).read_text(encoding="utf-8")


def _lands(key: str) -> bool:
    """Whether the entry of `key` registers its module through `Next._land`."""
    return f'Next._land("{key}"' in _entry_source(key)


def _build_entries() -> dict[str, str]:
    """Return the `build:next` esbuild entries, output name to source file."""
    script = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    command = script["scripts"]["build:next"]
    assert "--outdir=next/static/next" in command
    return dict(re.findall(r"(next(?:\.\w+)?\.min)=(\S+\.ts)", command))


class TestChunkLists:
    """Every list of the chunks names the same ones."""

    def test_module_keys_are_the_listed_chunks_and_each_landing_bundle(self) -> None:
        source = (CLIENT / "next.ts").read_text(encoding="utf-8")
        modules = _ts_strings(source, r"static #modules = Object\.fromEntries\(")
        landing = {key for key in DECLARED_CHUNK_KEYS if _lands(key)}
        assert modules == set(LISTED_CHUNK_KEYS) | landing

    def test_chunk_keys_name_every_shipped_chunk(self) -> None:
        assert set(LISTED_CHUNK_KEYS) == {
            "scripts",
            "stream",
            "ws",
            "csrf",
            "poll",
            "dev",
        }
        assert {
            "defer",
            "reload",
            "more",
            "reader",
            "router",
            "history",
            "intent",
        } == DECLARED_CHUNK_KEYS
        assert set(CHUNK_STATIC_PATHS) == {*LISTED_CHUNK_KEYS, *DECLARED_CHUNK_KEYS}

    def test_scripts_chunk_keys_match_the_payload_keys(self) -> None:
        source = (CLIENT / "chunks.ts").read_text(encoding="utf-8")
        assert _ts_strings(source, r"const CHUNK_KEYS =") == CHUNK_PAYLOAD_KEYS

    def test_build_writes_every_static_bundle(self) -> None:
        built = {f"next/{name}.js" for name in _build_entries()}
        assert built == _static_bundles()

    def test_each_listed_chunk_entry_lands_under_its_own_key(self) -> None:
        entries = _build_entries()
        for key in LISTED_CHUNK_KEYS:
            path = CHUNK_STATIC_PATHS[key]
            entry = entries[path.removeprefix("next/").removesuffix(".js")]
            source = (ROOT / entry).read_text(encoding="utf-8")
            assert f'Next._land("{key}"' in source, entry

    def test_a_declared_bundle_lands_or_installs_itself_on_the_runtime(self) -> None:
        for key in DECLARED_CHUNK_KEYS:
            source = _entry_source(key)
            if not _lands(key):
                assert "window.Next" in source, key
                assert "_land" not in source, key

    def test_a_self_installing_bundle_runs_once_and_is_never_fetched_lazily(
        self,
    ) -> None:
        source = (CLIENT / "next.ts").read_text(encoding="utf-8")
        modules = _ts_strings(source, r"static #modules = Object\.fromEntries\(")
        installing = {key for key in DECLARED_CHUNK_KEYS if not _lands(key)}
        assert {"reload", "more", "reader", "router", "history", "intent"} <= installing
        for key in installing:
            entry = _entry_source(key)
            assert f'once("{key}", ' in entry, key
            assert key not in modules, key

    def test_each_chunk_entry_is_named_by_its_key(self) -> None:
        entries = _build_entries()
        for key, path in CHUNK_STATIC_PATHS.items():
            assert path == f"next/next.{key}.min.js"
            entry = entries[f"next.{key}.min"]
            assert entry == f"next/client/entries/{key}.ts"

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
