import argparse
import ast
import importlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass


DOCS_CONTENT = pathlib.Path(__file__).resolve().parent / "content"

DIRECTIVE = re.compile(r"^(\s*)\.\.\s+(?:code-block|code|sourcecode)::\s*(\S+)?\s*$")
OPTION = re.compile(r"^\s+:([\w-]+):\s*(.*)$")
SETTINGS_BLOCK = re.compile(r"NEXT_FRAMEWORK\s*=\s*\{(.*?)\n\}", re.DOTALL)
SETTINGS_KEY = re.compile(r'^\s{4}"([A-Z_]+)"\s*:', re.MULTILINE)
TAG = re.compile(r"\{%-?\s*(#?/?\w+)")

PY_LANGS = frozenset({"python", "py", "python3"})
TEMPLATE_LANGS = frozenset({"jinja", "html", "django", "html+django"})

# The indent the pages give a directive body, so an outlier reads as a mistake.
BODY_INDENTS = frozenset({3, 4})

# next.dj block tags close with a slash, Django's own with an end- prefix.
SLASH_TAGS = frozenset(
    {"#component", "#slot", "#set_slot", "#use_style", "#use_script", "#template"}
)
END_TAGS = frozenset(
    {
        "autoescape",
        "block",
        "blocktrans",
        "blocktranslate",
        "cache",
        "comment",
        "filter",
        "for",
        "form",
        "if",
        "ifchanged",
        "language",
        "localize",
        "spaceless",
        "verbatim",
        "with",
        "zone",
    }
)

# Only the rules a snippet can be held to. A page carries excerpts, import-pattern
# demonstrations and imports kept for a side effect, so the dead-name and
# undefined-name rules would report the corpus rather than its defects.
# `unresolved_symbols` decides the half of undefined names an import line settles.
RUFF_RULES = "I,E9"

# A snippet is an excerpt, so the two blank lines the project wants after imports pad it
RUFF_CONFIG = "lint.isort.lines-after-imports=1"


@dataclass(frozen=True, slots=True)
class Block:
    """One code block lifted out of a reStructuredText page."""

    path: pathlib.Path
    line: int
    lang: str
    indent: int
    source: str


def _skip_options(lines: list[str], start: int) -> int:
    """Return the first line after the directive's options and blank lead-in."""
    cursor = start
    while cursor < len(lines) and (
        OPTION.match(lines[cursor]) or not lines[cursor].strip()
    ):
        cursor += 1
    return cursor


def _read_body(lines: list[str], start: int, base: int) -> tuple[list[str], int, int]:
    """Return the directive body, its own indent, and the line after it."""
    body: list[str] = []
    first = 0
    cursor = start
    while cursor < len(lines):
        line = lines[cursor]
        if not line.strip():
            body.append(line)
            cursor += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base:
            break
        first = first or indent
        body.append(line)
        cursor += 1
    while body and not body[-1].strip():
        body.pop()
    return body, first, cursor


def iter_blocks(path: pathlib.Path) -> Iterator[Block]:
    """Yield every directive-introduced code block in one page."""
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        match = DIRECTIVE.match(lines[index])
        if match is None:
            index += 1
            continue
        base = len(match.group(1))
        directive = index + 1
        body, indent, index = _read_body(lines, _skip_options(lines, index + 1), base)
        yield Block(
            path=path,
            line=directive,
            lang=(match.group(2) or "").lower(),
            indent=indent - base,
            source=textwrap.dedent("\n".join(body)),
        )


def parses_standalone(source: str) -> bool:
    """Whether the snippet is a whole module rather than an excerpt of one."""
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


def syntax_error(source: str) -> str | None:
    """Return the syntax error of a snippet, trying it as a body before failing."""
    last = ""
    for candidate in (
        source,
        "class _Holder:\n" + textwrap.indent(source, "    "),
        "def _holder():\n" + textwrap.indent(source, "    "),
    ):
        try:
            ast.parse(candidate)
        except SyntaxError as error:
            last = f"{error.msg} at snippet line {error.lineno}"
        else:
            return None
    return last


def template_error(source: str) -> str | None:
    """Return the first unbalanced block tag of a template snippet."""
    stack: list[str] = []
    for token in TAG.findall(source):
        if token in SLASH_TAGS or token in END_TAGS:
            stack.append(token)
            continue
        expected = (
            "#" + token[1:] if token.startswith("/") else token.removeprefix("end")
        )
        if expected == token:
            continue
        if not stack:
            return f"stray {{% {token} %}}"
        if stack[-1] != expected:
            return f"{{% {token} %}} closes {{% {stack[-1]} %}}"
        stack.pop()
    if stack:
        return "unclosed " + ", ".join(f"{{% {tag} %}}" for tag in stack)
    return None


def ruff_errors(block: Block) -> list[str]:
    """Return the ruff findings for a snippet that stands on its own."""
    executable = shutil.which("ruff")
    if executable is None:
        msg = "ruff is not on PATH, so the snippet gate cannot run"
        raise RuntimeError(msg)
    result = subprocess.run(  # noqa: S603
        [
            executable,
            "check",
            "--no-cache",
            "--quiet",
            "--output-format",
            "concise",
            "--select",
            RUFF_RULES,
            "--config",
            RUFF_CONFIG,
            "--stdin-filename",
            f"{block.path.stem}_{block.line}.py",
            "-",
        ],
        input=block.source,
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _setup_django() -> None:
    """Configure the minimum Django needs so `next` imports, once per process.

    The symbol and settings checks read the real package rather than a list kept
    beside it, and importing `next` needs settings in place.
    """
    from django.conf import settings  # noqa: PLC0415

    if settings.configured:
        return
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "next"],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {},
            }
        ],
        DATABASES={},
        NEXT_FRAMEWORK={},
    )
    importlib.import_module("django").setup()


def unknown_settings(source: str) -> list[str]:
    """Return a message per `NEXT_FRAMEWORK` key the framework does not define."""
    _setup_django()
    known = frozenset(importlib.import_module("next.conf.defaults").DEFAULTS)
    return [
        f"NEXT_FRAMEWORK has no key {key!r}"
        for match in SETTINGS_BLOCK.finditer(source)
        for key in SETTINGS_KEY.findall(match.group(1))
        if key not in known
    ]


def _next_imports(tree: ast.Module) -> Iterator[tuple[str, tuple[str, ...]]]:
    """Yield each `next` module a snippet imports beside the names it takes from it."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "next" or alias.name.startswith("next."):
                    yield alias.name, ()
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "next" or module.startswith("next."):
                yield module, tuple(alias.name for alias in node.names)


def unresolved_symbols(source: str) -> list[str]:
    """Return a message per `next` name a snippet imports that does not exist.

    An import line is decidable whatever surrounds the snippet, so this is the
    half of undefined-name checking a corpus of excerpts can carry.
    """
    if "next" not in source:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    _setup_django()
    found: list[str] = []
    for module, names in _next_imports(tree):
        try:
            imported = importlib.import_module(module)
        except ImportError:
            found.append(f"no module {module}")
            continue
        found.extend(
            f"{module} has no {name}" for name in names if not hasattr(imported, name)
        )
    return found


def _python_findings(block: Block) -> list[str]:
    """Return every finding for one Python block."""
    error = syntax_error(block.source)
    if error is not None:
        return [f"python does not parse, {error}"]
    found = unresolved_symbols(block.source) + unknown_settings(block.source)
    if parses_standalone(block.source):
        found.extend(ruff_errors(block))
    return found


def _markup_findings(block: Block) -> list[str]:
    """Return every finding for one template or data block."""
    if block.lang in TEMPLATE_LANGS:
        error = template_error(block.source)
        return [] if error is None else [f"template tags unbalanced, {error}"]
    if block.lang == "json":
        try:
            json.loads(block.source)
        except ValueError as error:
            return [f"json does not parse, {error}"]
    return []


def block_findings(block: Block) -> list[str]:
    """Return every finding for one code block, each prefixed with its location."""
    if not block.source.strip():
        return []
    found: list[str] = []
    if "\t" in block.source:
        found.append("tab in a code block")
    if block.indent not in BODY_INDENTS:
        found.append(f"block indented {block.indent} under its directive, pages use 3")
    if block.lang in PY_LANGS:
        found.extend(_python_findings(block))
    else:
        found.extend(_markup_findings(block))
    return [f"{block.path}:{block.line}: {message}" for message in found]


def check(paths: list[pathlib.Path]) -> list[str]:
    """Return every finding across the pages under `paths`."""
    findings: list[str] = []
    for root in paths:
        pages = sorted(root.rglob("*.rst")) if root.is_dir() else [root]
        for page in pages:
            for block in iter_blocks(page):
                findings.extend(block_findings(block))
    return findings


def main(argv: list[str]) -> int:
    """Check every code block under the given paths and report the findings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=pathlib.Path, default=[DOCS_CONTENT])
    args = parser.parse_args(argv)
    findings = check(args.paths or [DOCS_CONTENT])
    for finding in findings:
        sys.stdout.write(f"{finding}\n")
    if findings:
        sys.stderr.write(f"\n{len(findings)} snippet findings\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
