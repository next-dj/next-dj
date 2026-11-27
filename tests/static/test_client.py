import re
from pathlib import Path

import pytest

from next.static.protocol import ROOT, values


_CLIENT = ROOT / "next" / "client"
_GENERATED = "protocol.gen.ts"
_REGISTRY = "protocol.ts"
_NAMES = re.compile(r"data-next-|X-Next-")
_DISTINCT = re.compile(
    r"\$[a-z]+|_[a-z_]+|next-[a-z]+|nx:s|application/vnd\.next\..*|text/event-stream"
)

_SOURCE = pytest.mark.skipif(not _CLIENT.exists(), reason="needs the source checkout")


_TOKEN = re.compile(
    r"(?P<line>//[^\n]*)|(?P<block>/\*.*?\*/)|(?P<plain>\"(?:\\.|[^\"\\\n])*\""
    r"|'(?:\\.|[^'\\\n])*')|(?P<tick>`)|(?P<open>\{)|(?P<close>\})|(?P<gap>\n)",
    re.DOTALL,
)
_TEMPLATE = re.compile(r"(?:\\.|[^`\\$]|\$(?!\{))*", re.DOTALL)


def _strings(text: str) -> list[tuple[int, str]]:
    """Return each string and template text of `text` with its line, comments skipped."""
    found: list[tuple[int, str]] = []
    braces: list[int] = []
    line, index = 1, 0

    def template(at: int) -> int:
        nonlocal line
        chunk = _TEMPLATE.match(text, at)
        assert chunk is not None
        found.append((line, chunk.group()))
        line += chunk.group().count("\n")
        if text.startswith("${", chunk.end()):
            braces.append(0)
            return chunk.end() + 2
        return chunk.end() + 1

    while index < len(text):
        token = _TOKEN.search(text, index)
        if token is None:
            break
        kind = token.lastgroup
        index = token.end()
        if kind == "plain":
            found.append((line, token.group()[1:-1]))
        elif kind == "tick":
            index = template(index)
        elif kind == "open" and braces:
            braces[-1] += 1
        elif kind == "close" and braces:
            if braces[-1]:
                braces[-1] -= 1
            else:
                braces.pop()
                index = template(index)
        line += token.group().count("\n")
    return found


def _guarded() -> dict[str, str]:
    """Map each protocol value a literal must not spell out to its constant."""
    return {
        value: name
        for name, value in values().items()
        if isinstance(value, str)
        and (_NAMES.search(value) or _DISTINCT.fullmatch(value))
    }


def _violations(path: Path, text: str, guarded: dict[str, str]) -> list[str]:
    found = []
    for line, literal in _strings(text):
        constant = guarded.get(literal)
        if constant is None and path.name != _REGISTRY and _NAMES.search(literal):
            constant = "the constant of the attribute or header it names"
        if constant is not None:
            found.append(
                f"{path.relative_to(ROOT)}:{line} spells {literal!r}, use {constant}"
            )
    return found


def _sources() -> list[Path]:
    return sorted(
        found
        for found in _CLIENT.rglob("*.ts")
        if found.name != _GENERATED
        and not found.name.endswith(".test.ts")
        and found.name != "test-doubles.ts"
    )


class TestClientLiterals:
    """The client spells each protocol string once, in the generated module."""

    @_SOURCE
    def test_no_client_module_spells_a_protocol_string(self) -> None:
        guarded = _guarded()
        found = [
            message
            for source in _sources()
            for message in _violations(
                source, source.read_text(encoding="utf-8"), guarded
            )
        ]
        assert not found, "\n".join(
            [
                "a protocol string is written twice, import its generated constant",
                *found,
            ]
        )

    @_SOURCE
    def test_the_loading_selector_is_the_state_attribute_spelled_out(self) -> None:
        registry = (_CLIENT / _REGISTRY).read_text(encoding="utf-8")
        state = values()["ATTR_STATE"]
        assert f'LOADING = "[{state}=loading]";' in registry

    def test_the_scanner_names_file_line_and_constant(self) -> None:
        text = (
            '// "data-next-zone" in a comment\n'
            "const a = `x${b}[data-next-state=loading]`;\n"
            "const c = nest(`${d(`a`)}`) + '$stream';\n"
            '/* "X-Next-Zone"\n*/\n'
            'const e = "ok";\n'
        )
        path = _CLIENT / "probe.ts"
        found = _violations(path, text, _guarded())
        assert found == [
            (
                "next/client/probe.ts:2 spells '[data-next-state=loading]', "
                "use the constant of the attribute or header it names"
            ),
            "next/client/probe.ts:3 spells '$stream', use PAYLOAD_STREAM",
        ]

    def test_a_generated_value_is_named_by_its_constant(self) -> None:
        path = _CLIENT / "probe.ts"
        found = _violations(path, 'x("data-next-state")', _guarded())
        assert found == [
            "next/client/probe.ts:1 spells 'data-next-state', use ATTR_STATE"
        ]
