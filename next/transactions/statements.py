"""Lexical classifier that tells what one SQL statement does to a transaction."""

import enum
import re
from typing import Final


class StatementKind(enum.IntEnum):
    """What one SQL statement does, as the lazy savepoint decides on it."""

    CONTROL = 0
    STANDALONE = 1
    SESSION = 2
    READ = 3
    WRITE = 4


_LEAD: Final = re.compile(r"(?:\s|\(|/\*.*?\*/|--[^\n]*+)*+([A-Za-z]+)", re.DOTALL)
_TOKEN: Final = re.compile(
    r"\s++|/\*.*?\*/|--[^\n]*+|'(?:[^']|'')*+'|\"(?:[^\"]|\"\")*+\"|`(?:[^`]|``)*+`"
    r"|\$(?P<tag>\w*)\$.*?\$(?P=tag)\$|(?P<word>[A-Za-z_][\w$]*+)|(?P<bad>['\"`]|/\*)|.",
    re.DOTALL,
)
_HINT: Final = re.compile(
    r"\b(?:INTO|UPDATE|SHARE|INSERT|DELETE|MERGE)\b", re.IGNORECASE
)
_LEADS: Final = {
    "START": StatementKind.CONTROL,
    "COMMIT": StatementKind.CONTROL,
    "END": StatementKind.CONTROL,
    "ROLLBACK": StatementKind.CONTROL,
    "RELEASE": StatementKind.CONTROL,
    "ABORT": StatementKind.CONTROL,
    "VACUUM": StatementKind.STANDALONE,
    "ATTACH": StatementKind.STANDALONE,
    "DETACH": StatementKind.STANDALONE,
    "SAVEPOINT": StatementKind.SESSION,
    "SET": StatementKind.SESSION,
    "RESET": StatementKind.SESSION,
    "PRAGMA": StatementKind.SESSION,
    "SELECT": StatementKind.READ,
    "SHOW": StatementKind.READ,
    "EXPLAIN": StatementKind.READ,
    "WITH": StatementKind.READ,
    "VALUES": StatementKind.READ,
    "TABLE": StatementKind.READ,
    "DESCRIBE": StatementKind.READ,
    "DESC": StatementKind.READ,
}
_FOLLOWED: Final = frozenset(
    {"ALTER", "BEGIN", "CLUSTER", "CREATE", "DISCARD", "DROP", "REINDEX"}
)
_BEGIN_MODES: Final = frozenset(
    {
        "DEFERRABLE",
        "DEFERRED",
        "EXCLUSIVE",
        "IMMEDIATE",
        "ISOLATION",
        "NOT",
        "READ",
        "TRAN",
        "TRANSACTION",
        "WORK",
    }
)
_ALONE: Final = frozenset({"DATABASE", "SUBSCRIPTION", "SYSTEM", "TABLESPACE"})
_CHANGES: Final = frozenset({"DELETE", "INSERT", "INTO", "MERGE"})
_LOCKED: Final = frozenset({"SHARE", "UPDATE"})
_LOCKING: Final = frozenset({"FOR", "IN", "KEY"})
_LOOSE: Final = frozenset({"EXPLAIN", "WITH"})


def _text(sql: object) -> str | None:
    """Return the statement as text, `None` for one that is neither text nor UTF-8."""
    if isinstance(sql, str):
        return sql
    if isinstance(sql, bytes):
        try:
            return sql.decode()
        except UnicodeDecodeError:
            return None
    return None


def _words(text: str, start: int) -> tuple[str, ...] | None:
    """Return the bare words after `start`, `None` if a quote or comment never ends."""
    words: list[str] = []
    for token in _TOKEN.finditer(text, start):
        group = token.lastgroup
        if group == "bad":
            return None
        if group == "word":
            words.append(token.group("word").upper())
    return tuple(words)


def _changes(lead: str, words: tuple[str, ...]) -> bool:
    """Whether a read changes data or locks rows, judged by its bare words."""
    loose = lead in _LOOSE
    before = ""
    for word in words:
        if word in _CHANGES or (word in _LOCKED and (loose or before in _LOCKING)):
            return True
        before = word
    return False


def _read(text: str, start: int, lead: str) -> StatementKind:
    if _HINT.search(text, start) is None:
        return StatementKind.READ
    words = _words(text, start)
    if words is None or _changes(lead, words):
        return StatementKind.WRITE
    return StatementKind.READ


def _followed(lead: str, words: tuple[str, ...]) -> StatementKind:
    """Classify a statement whose leading keyword alone does not decide."""
    if lead == "BEGIN":
        control = not words or words[0] in _BEGIN_MODES
        return StatementKind.CONTROL if control else StatementKind.WRITE
    if lead == "DISCARD":
        return StatementKind.STANDALONE if "ALL" in words else StatementKind.SESSION
    if lead == "CLUSTER":
        alone = set(words) <= {"VERBOSE"}
    else:
        alone = "CONCURRENTLY" in words or not _ALONE.isdisjoint(words[:2])
    return StatementKind.STANDALONE if alone else StatementKind.WRITE


def statement_kind(sql: object) -> StatementKind:
    """Classify a statement by its leading keyword, an unknown one counting as a write.

    Comments, quoted text and quoted names never decide, so only real keywords count.
    """
    text = _text(sql)
    if text is None:
        return StatementKind.WRITE
    lead = _LEAD.match(text)
    if lead is None:
        return StatementKind.WRITE
    word = lead.group(1).upper()
    kind = _LEADS.get(word)
    if kind is StatementKind.READ:
        return _read(text, lead.end(), word)
    if kind is not None:
        return kind
    if word not in _FOLLOWED:
        return StatementKind.WRITE
    words = _words(text, lead.end())
    return StatementKind.WRITE if words is None else _followed(word, words)


def nests(kind: StatementKind, sql: object) -> bool:
    """Whether the statement opens a savepoint, which every later one nests inside."""
    if kind is not StatementKind.SESSION:
        return False
    lead = _LEAD.match(_text(sql) or "")
    return lead is not None and lead.group(1).upper() == "SAVEPOINT"


__all__ = ["StatementKind", "nests", "statement_kind"]
