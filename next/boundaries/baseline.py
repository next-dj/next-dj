"""The `boundaries.toml` ratchet, the debt a project records and may only shrink.

Only `manage.py next boundaries --refreeze` writes the file, a check never does.
"""

import json
import tomllib
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .errors import BaselineError
from .rules import GROWN, RECORDABLE, STALE, Finding


FILENAME: Final = "boundaries.toml"
VERSION: Final = 1

_TOP_KEYS: Final = frozenset({"version", "violation"})
_ENTRY_KEYS: Final = frozenset({"file", "code", "count"})

type Key = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Debt:
    """The findings of one code in one file against what the ratchet records."""

    app: str
    code: str
    file: str
    found: int
    recorded: int


@dataclass(frozen=True, slots=True)
class Ratchet:
    """The findings no record covers, the broken records and every debt row."""

    shown: tuple[Finding, ...]
    problems: tuple[Finding, ...]
    debts: tuple[Debt, ...]


def baseline_path(base_dir: Path) -> Path:
    """Return where the ratchet of the project under `base_dir` lives."""
    return base_dir / FILENAME


def _entry(path: Path, index: int, row: object) -> tuple[Key, int]:
    """Return the key and count of one `[[violation]]` table."""
    where = f"Entry {index} of [[violation]]"
    if not isinstance(row, dict) or set(row) != _ENTRY_KEYS:
        keys = ", ".join(sorted(_ENTRY_KEYS))
        raise BaselineError(path, f"{where} must hold exactly the keys {keys}.")
    file, code, count = row["file"], row["code"], row["count"]
    if not isinstance(file, str) or not file:
        raise BaselineError(path, f"{where} names no file.")
    if not isinstance(code, str) or code not in RECORDABLE:
        raise BaselineError(path, f"{where} names {code!r}, which is no boundary code.")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise BaselineError(path, f"{where} must count at least one finding.")
    return (file, code), count


def _entries(path: Path, data: Mapping[str, object]) -> dict[Key, int]:
    """Return the counts a parsed ratchet records, refusing any other shape."""
    unknown = sorted(set(data) - _TOP_KEYS)
    if unknown:
        raise BaselineError(path, f"It has the unknown keys {', '.join(unknown)}.")
    if data.get("version") != VERSION:
        raise BaselineError(path, f"It must declare version = {VERSION}.")
    rows = data.get("violation", [])
    if not isinstance(rows, list):
        raise BaselineError(path, "violation must be an array of tables.")
    counts: dict[Key, int] = {}
    for index, row in enumerate(rows, start=1):
        key, count = _entry(path, index, row)
        if key in counts:
            raise BaselineError(path, f"It records {key[1]} in {key[0]} twice.")
        counts[key] = count
    return counts


def read(path: Path) -> dict[Key, int] | None:
    """Return the counts the ratchet at `path` records, `None` when there is none."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise BaselineError(path, str(exc)) from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise BaselineError(path, f"It is no valid TOML. {exc}") from exc
    return _entries(path, data)


def _quote(value: str) -> str:
    """Return `value` as a TOML basic string, whose escapes JSON shares."""
    return json.dumps(value, ensure_ascii=False)


def dump(counts: Mapping[Key, int]) -> str:
    """Return the ratchet text for `counts`, sorted by file and then by code."""
    lines = [f"version = {VERSION}"]
    for (file, code), count in sorted(counts.items()):
        lines.extend(
            (
                "",
                "[[violation]]",
                f"file = {_quote(file)}",
                f"code = {_quote(code)}",
                f"count = {count}",
            )
        )
    return "\n".join(lines) + "\n"


def write(path: Path, counts: Mapping[Key, int]) -> None:
    """Write the ratchet for `counts` to `path`."""
    try:
        path.write_text(dump(counts), encoding="utf-8")
    except OSError as exc:
        raise BaselineError(path, str(exc)) from exc


def tally(findings: Iterable[Finding]) -> Counter[Key]:
    """Count the recordable findings per file and code."""
    return Counter(
        (finding.file, finding.code)
        for finding in findings
        if finding.code in RECORDABLE
    )


def _grown(key: Key, app: str, found: list[Finding], recorded: int) -> Finding:
    """Return the error of a file holding more findings than its record."""
    file, code = key
    lines = ", ".join(str(finding.line) for finding in found)
    return Finding(
        file,
        0,
        GROWN,
        app,
        f"Findings of {code} in the file: {len(found)}, recorded in boundaries.toml: "
        f"{recorded}. Their lines: {lines}.",
    )


def _stale(key: Key, app: str, found: int, recorded: int) -> Finding:
    """Return the error of a record larger than what its file still holds."""
    file, code = key
    return Finding(
        file,
        0,
        STALE,
        app,
        f"Findings of {code} in {file} recorded in boundaries.toml: {recorded}, "
        f"remaining: {found}.",
    )


@dataclass(frozen=True, slots=True)
class Purview:
    """What of the recorded debt the installed apps can judge.

    `away` holds each recorded file on disk that no installed app of the project holds.
    """

    base_dir: Path | None = None
    away: frozenset[str] = frozenset()
    reduced: bool = False

    def may_shrink(self, file: str) -> bool:
        """Return whether a file on disk may hold fewer findings for the apps left out.

        A finding can vanish with the app its edge reached, so only growth is judged.
        """
        return (
            self.reduced
            and self.base_dir is not None
            and (self.base_dir / file).is_file()
        )

    def set_aside(self, file: str) -> bool:
        """Return whether a recorded file belongs to an app the settings leave out."""
        return file in self.away


WHOLE: Final = Purview()


def judged(
    base_dir: Path | None,
    recorded: Mapping[Key, int] | None,
    app_of: Callable[[str], str],
    *,
    dropping: bool,
) -> Purview:
    """Return what the ratchet may judge under the settings of this run.

    A record of a file no installed app holds, or `dropping`, shows apps left out.
    """
    if base_dir is None or not recorded:
        return WHOLE
    away = frozenset(
        file
        for file, _code in recorded
        if (base_dir / file).is_file() and not app_of(file)
    )
    return Purview(base_dir, away, reduced=bool(away) or dropping)


def compare(
    findings: Iterable[Finding],
    recorded: Mapping[Key, int] | None,
    app_of: Callable[[str], str],
    purview: Purview = WHOLE,
) -> Ratchet:
    """Split the findings into those no record covers and the records that broke.

    A deploy that leaves apps out judges only growth and sets aside their records.
    """
    grouped: dict[Key, list[Finding]] = defaultdict(list)
    shown: list[Finding] = []
    for finding in findings:
        if finding.code in RECORDABLE:
            grouped[finding.file, finding.code].append(finding)
        else:
            shown.append(finding)
    records = recorded or {}
    problems: list[Finding] = []
    debts: list[Debt] = []
    for key in sorted(grouped.keys() | records.keys()):
        found = grouped.get(key, [])
        if not found and purview.set_aside(key[0]):
            continue
        count = records.get(key, 0)
        app = found[0].app if found else app_of(key[0])
        debts.append(Debt(app, key[1], key[0], len(found), count))
        if count == 0:
            shown.extend(found)
        elif len(found) > count:
            problems.append(_grown(key, app, found, count))
        elif len(found) < count and not purview.may_shrink(key[0]):
            problems.append(_stale(key, app, len(found), count))
    return Ratchet(tuple(sorted(shown)), tuple(problems), tuple(debts))


def _kept(key: Key, count: int, found: Mapping[Key, int], purview: Purview) -> int:
    """Return what a refreeze keeps of one record, all of it where it cannot tell.

    A deploy that leaves apps out keeps its records, so the next full refreeze judges.
    """
    if purview.may_shrink(key[0]):
        return count
    return min(count, found.get(key, 0))


def shrink(
    findings: Iterable[Finding],
    recorded: Mapping[Key, int] | None,
    purview: Purview = WHOLE,
) -> tuple[dict[Key, int], int]:
    """Return the counts a refreeze writes and how many findings stay unrecorded.

    With no file every finding is recorded, and with one a record only shrinks.
    """
    found = tally(findings)
    if recorded is None:
        return dict(found), 0
    counts = {
        key: kept
        for key, count in recorded.items()
        if (kept := _kept(key, count, found, purview)) > 0
    }
    unrecorded = sum(
        max(count - recorded.get(key, 0), 0) for key, count in found.items()
    )
    return counts, unrecorded


__all__ = [
    "FILENAME",
    "VERSION",
    "WHOLE",
    "Debt",
    "Key",
    "Purview",
    "Ratchet",
    "baseline_path",
    "compare",
    "dump",
    "judged",
    "read",
    "shrink",
    "tally",
    "write",
]
