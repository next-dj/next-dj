"""Check that reStructuredText prose keeps one sentence per line.

Neither ``doc8`` nor ``sphinx-build -W`` sees semantic newlines, so this gate
covers the drift they let through.
"""

import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path


DOCS_CONTENT = Path(__file__).resolve().parent / "content"

# A token shorter than this ends in a period as an initial, not as a sentence.
MIN_TOKEN_LENGTH = 2

# How much of an offending sentence to quote back in a message.
SNIPPET_LENGTH = 40

# Directive bodies that hold code, data, or paths rather than prose.
LITERAL_DIRECTIVES = frozenset(
    {
        "code",
        "code-block",
        "csv-table",
        "glossary",
        "graphviz",
        "highlight",
        "image",
        "include",
        "literalinclude",
        "math",
        "mermaid",
        "parsed-literal",
        "raw",
        "toctree",
    }
)

# Words that end in a period without ending a sentence.
ABBREVIATIONS = frozenset({"al", "cf", "etc", "vs"})

DIRECTIVE_RE = re.compile(r"^(?P<indent>\s*)\.\.\s+(?P<name>[a-z0-9_:+-]+)::")
COMMENT_RE = re.compile(r"^\s*\.\.(\s|$)")
FIELD_RE = re.compile(r"^\s*:[^:\s][^:]*:(\s|$)")
DOCTEST_RE = re.compile(r"^\s*(>>>|\.\.\.)\s")
ADORNMENT_RE = re.compile(r"^([=\-~^\"'`#*+_:.<>])\1{2,}\s*$")
TABLE_RULE_RE = re.compile(r"^\s*[+|][=+|\- ]*[+|]\s*$")

# One or more nested markers, so a "* - " list-table row exposes its cell body.
BULLET_RE = re.compile(
    r"^(?P<indent>\s*)(?:(?:[*+-]|\(?(?:\d{1,3}|[a-zA-Z]|[ivxIVX]{2,4}|#)[.)])[ \t]+)+"
)

# Inline constructs whose contents are never prose sentences.
INLINE_RE = re.compile(
    r"``.*?``|:[a-z0-9_:+-]+:`[^`]*`|`[^`]*`_{0,2}|\|[^|\s][^|]*\||https?://\S+"
)

# A sentence may also close on an inline literal, a role, or a bracket.
SENTENCE_END_RE = re.compile(
    r"(?P<token>[\w.'-]*[\w']|[`)\]\"'])(?P<end>[.!?])(?P<tail>[\"')\]]*)\s+"
)
SENTENCE_START_RE = re.compile(r"[A-Z`]")
TERMINATED_RE = re.compile(r"([.!?][\"')\]]*|::)$")
UNFINISHED_RE = re.compile(r"[\w,;:]$")


def blank_inline(match: re.Match[str]) -> str:
    """Keep the delimiters of an inline construct and blank out what they wrap."""
    text = match.group(0)
    return text[0] + "x" * (len(text) - 2) + text[-1]


def mask_inline(text: str) -> str:
    """Blank out inline literals, roles, links, and URLs without moving any column."""
    return INLINE_RE.sub(blank_inline, text)


def is_abbreviation(token: str) -> bool:
    """Report whether a period-terminated word is an abbreviation, version, or path."""
    return (
        len(token) < MIN_TOKEN_LENGTH or "." in token or token.lower() in ABBREVIATIONS
    )


@dataclass(frozen=True, slots=True)
class Line:
    """One physical line of a document, with the structure the two checks need."""

    number: int
    raw: str
    text: str = field(init=False)
    indent: int = field(init=False)
    is_marker: bool = field(init=False)
    content_indent: int = field(init=False)
    body: str = field(init=False)

    def __post_init__(self) -> None:
        """Derive the indentation and bullet structure from the raw line."""
        stripped = self.raw.rstrip()
        text = stripped.lstrip()
        bullet = BULLET_RE.match(stripped)
        content_indent = bullet.end() if bullet else len(stripped) - len(text)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "indent", len(stripped) - len(text))
        object.__setattr__(self, "is_marker", bullet is not None)
        object.__setattr__(self, "content_indent", content_indent)
        object.__setattr__(self, "body", stripped[content_indent:] if bullet else text)


def is_markup(line: Line) -> bool:
    """Report whether a line is reStructuredText markup rather than prose."""
    return bool(
        DIRECTIVE_RE.match(line.raw)
        or COMMENT_RE.match(line.raw)
        or FIELD_RE.match(line.raw)
        or DOCTEST_RE.match(line.raw)
        or ADORNMENT_RE.match(line.text)
        or TABLE_RULE_RE.match(line.raw)
    )


def is_literal_directive(match: re.Match[str]) -> bool:
    """Report whether a directive owns a body of code, data, or generated output."""
    name = match.group("name")
    return name.startswith("auto") or name.rsplit(":", 1)[-1] in LITERAL_DIRECTIVES


def prose_lines(lines: Sequence[Line]) -> list[Line]:
    """Drop every line that sits inside a literal block or is markup itself."""
    kept: list[Line] = []
    skip_base: int | None = None
    for line in lines:
        if skip_base is not None:
            if not line.text or line.indent > skip_base:
                continue
            skip_base = None
        if not line.text:
            continue
        directive = DIRECTIVE_RE.match(line.raw)
        if directive is not None:
            if is_literal_directive(directive):
                skip_base = len(directive.group("indent"))
            continue
        if is_markup(line):
            continue
        # A paragraph ending in "::" introduces an indented literal block.
        if line.text.endswith("::"):
            skip_base = line.indent
            continue
        kept.append(line)
    return kept


def sentence_breaks(text: str) -> Iterator[re.Match[str]]:
    """Yield every match where a sentence ends and the next one begins in one text."""
    masked = mask_inline(text)
    for match in SENTENCE_END_RE.finditer(masked):
        token = match.group("token")
        if match.group("end") == "." and token[-1].isalnum() and is_abbreviation(token):
            continue
        if SENTENCE_START_RE.match(masked[match.end() :]):
            yield match


def run_on_sentences(line: Line) -> Iterator[str]:
    """Yield the opening words of every sentence sharing a line with the one before."""
    for match in sentence_breaks(line.body):
        yield line.body[match.end() :][:SNIPPET_LENGTH].rstrip()


def continues(previous: Line, current: Line) -> bool:
    """Report whether a line is the tail of the paragraph line directly above it."""
    return (
        current.number == previous.number + 1
        and not current.is_marker
        and current.indent == previous.content_indent
    )


def is_wrapped(previous: Line, current: Line) -> bool:
    """Report whether one sentence was split across two physical lines."""
    if not continues(previous, current):
        return False
    tail = mask_inline(previous.body).rstrip()
    if TERMINATED_RE.search(tail):
        return False
    # An unpunctuated tail is only a wrap when one of the two lines says so.
    return bool(UNFINISHED_RE.search(tail)) or current.body[:1].islower()


def check_file(path: Path) -> list[str]:
    """Collect every semantic newline violation in one document."""
    source = path.read_text("utf-8").splitlines()
    kept = prose_lines([Line(n, raw) for n, raw in enumerate(source, 1)])
    found: list[tuple[int, str]] = [
        (line.number, f"second sentence on the same line, starting {snippet!r}")
        for line in kept
        for snippet in run_on_sentences(line)
    ]
    found += [
        (previous.number, "sentence wrapped onto the next line")
        for previous, current in pairwise(kept)
        if is_wrapped(previous, current)
    ]
    return [f"{path}:{number}: {message}" for number, message in sorted(found)]


def collect(paths: Sequence[str]) -> list[Path]:
    """Expand the command line into the set of documents to check."""
    if not paths:
        return sorted(DOCS_CONTENT.rglob("*.rst"))
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        found.extend(sorted(path.rglob("*.rst")) if path.is_dir() else [path])
    return [path for path in found if path.suffix == ".rst"]


def main(argv: Sequence[str]) -> int:
    """Print every violation and return a non-zero code when any was found."""
    problems = [problem for path in collect(argv) for problem in check_file(path)]
    for problem in problems:
        sys.stdout.write(problem + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
