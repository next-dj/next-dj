import ast
import re
from pathlib import Path

import pytest

from next.partial.keys import NAV_ATTR
from next.router.speculation import GUARDED
from next.static.protocol import ROOT, values


_NEXT = ROOT / "next"
_NAMES = r"data-next-[a-z][a-z-]*|X-Next-[A-Za-z][A-Za-z-]*|(?<![\w])_nojs\b"
_NAMES += r"|(?<![\w])_restore\b"
_DEFAULTS = "next/conf/defaults.py"
_PROSE_KEYWORDS = frozenset({"msg", "hint"})
_OFF_PREFIX = re.compile(r"const OFF = `(?P<prefix>.*?)\$\{ROUTER_OFF\}`;")
_NAME = re.compile(r"\$\{(?P<name>[A-Z_]+)\}")
_TEMPLATE_SUFFIXES = frozenset({".djx", ".html"})

_SOURCE = pytest.mark.skipif(
    not (ROOT / "next" / "client" / "router.ts").exists(),
    reason="needs the source checkout",
)


def _vocabulary() -> re.Pattern[str]:
    payload = [
        re.escape(value)
        for value in values().values()
        if isinstance(value, str) and re.fullmatch(r"\$[a-z]+", value)
    ]
    return re.compile("|".join([_NAMES, *payload]))


def _sources() -> list[Path]:
    return sorted(
        found
        for found in _NEXT.rglob("*.py")
        if found.relative_to(ROOT).as_posix() != _DEFAULTS
    )


def _declaration(node: ast.stmt) -> tuple[str, ast.Constant] | None:
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign):
        targets, value = [node.target], node.value
    else:
        return None
    if len(targets) != 1 or not isinstance(targets[0], ast.Name):
        return None
    if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
        return None
    return targets[0].id, value


def _declared(sources: list[Path], vocabulary: re.Pattern[str]) -> dict[str, list[str]]:
    """Map each protocol string to the module constants that spell it out."""
    declared: dict[str, list[str]] = {}
    for source in sources:
        module = source.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")
        for node in ast.parse(source.read_text(encoding="utf-8")).body:
            found = _declaration(node)
            if found is not None and vocabulary.fullmatch(found[1].value):
                declared.setdefault(found[1].value, []).append(f"{module}.{found[0]}")
    return declared


def _allowed(tree: ast.Module) -> set[int]:
    """Return the string nodes that are prose or the declaration of a constant."""
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            allowed.add(id(node.value))
        elif isinstance(node, ast.keyword) and node.arg in _PROSE_KEYWORDS:
            allowed.update(id(part) for part in ast.walk(node.value))
    for node in tree.body:
        found = _declaration(node)
        if found is not None:
            allowed.add(id(found[1]))
    return allowed


def _violations(
    sources: list[Path], vocabulary: re.Pattern[str], declared: dict[str, list[str]]
) -> list[str]:
    found: list[str] = []
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        allowed = _allowed(tree)
        where = source.relative_to(ROOT) if source.is_relative_to(ROOT) else source
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in allowed:
                continue
            found.extend(
                f"{where}:{node.lineno} spells {name!r}, {_advice(name, declared)}"
                for name in dict.fromkeys(vocabulary.findall(node.value))
            )
    return found


def _advice(name: str, declared: dict[str, list[str]]) -> str:
    if name in declared:
        return f"use {declared[name][0]}"
    return "declare a constant for it next to its siblings in next/partial/keys.py"


class TestWireLiterals:
    """The protocol strings of the wire have one spelling, a module constant."""

    def test_no_source_spells_a_protocol_string_apart_from_its_constant(self) -> None:
        sources = _sources()
        vocabulary = _vocabulary()
        found = _violations(sources, vocabulary, _declared(sources, vocabulary))
        assert not found, "\n".join(found)

    def test_each_protocol_string_is_declared_once(self) -> None:
        sources = _sources()
        declared = _declared(sources, _vocabulary())
        twice = {name: where for name, where in declared.items() if len(where) > 1}
        assert not twice

    def test_the_scan_names_the_file_the_line_and_the_constant_to_use(
        self, tmp_path: Path
    ) -> None:
        stray = tmp_path / "stray.py"
        stray.write_text(
            '"""Mentions data-next-state in prose."""\n'
            "\n"
            'KNOWN = "data-next-state"\n'
            "html = f'<b data-next-state=\"x\" data-next-mystery>{1}</b>'\n"
            'failure = Error(msg="X-Next-Zone is bad", hint="_nojs")\n',
            encoding="utf-8",
        )
        vocabulary = _vocabulary()
        declared = {"data-next-state": ["next.wire.STATE_ATTR"]}
        found = _violations([stray], vocabulary, declared)
        assert [line.split(" spells ")[0].rsplit(":", 1)[1] for line in found] == [
            "4",
            "4",
        ]
        assert "use next.wire.STATE_ATTR" in found[0]
        assert "declare a constant" in found[1]

    def test_the_declared_names_cover_the_wire_vocabulary(self) -> None:
        declared = _declared(_sources(), _vocabulary())
        assert declared["data-next-state"] == ["next.wire.STATE_ATTR"]
        assert declared["X-Next-Request"] == ["next.wire.REQUEST_FLAG"]
        assert declared["_nojs"] == ["next.partial.keys.NOSCRIPT_PARAM"]
        assert declared["$csrf"] == ["next.static.runtime.CSRF_PAYLOAD_KEY"]

    def test_the_framework_ships_no_template_spelling_an_unknown_name(self) -> None:
        declared = _declared(_sources(), _vocabulary())
        shipped = [
            found for found in _NEXT.rglob("*") if found.suffix in _TEMPLATE_SUFFIXES
        ]
        unknown = [
            f"{found.relative_to(ROOT)} spells {name!r}"
            for found in shipped
            for name in re.findall(_NAMES, found.read_text(encoding="utf-8"))
            if name not in declared
        ]
        assert not unknown

    def test_the_settings_default_spells_the_nav_mark_as_the_constant_does(
        self,
    ) -> None:
        text = (ROOT / _DEFAULTS).read_text(encoding="utf-8")
        assert f"[{NAV_ATTR}]" in text


class TestRouterSelectors:
    """The client guards a link by a prefix of the selector the server guards by."""

    @_SOURCE
    def test_the_prefix_of_the_client_selector_opens_the_guarded_one(self) -> None:
        text = (ROOT / "next" / "client" / "router.ts").read_text(encoding="utf-8")
        found = _OFF_PREFIX.search(text)
        assert found is not None
        exports = values()
        prefix = _NAME.sub(lambda name: str(exports[name["name"]]), found["prefix"])
        assert GUARDED.startswith(prefix)
