"""Lexical classifier that tells what one SQL statement does to a transaction."""

import enum
import re
from typing import Final, NamedTuple


class StatementKind(enum.IntEnum):
    """What one SQL statement does, as the lazy savepoint decides on it."""

    CONTROL = 0
    STANDALONE = 1
    SESSION = 2
    READ = 3
    WRITE = 4


def _lexer(text: str) -> re.Pattern[str]:
    """Return the token pattern that reads a quoted string as `text` matches it."""
    return re.compile(
        rf"\s++|/\*.*?\*/|--[^\n]*+|(?P<text>{text})"
        r"|(?P<name>\"(?:[^\"]|\"\")*+\"|`(?:[^`]|``)*+`)"
        r"|\$(?P<tag>\w*)\$.*?\$(?P=tag)\$|(?P<word>[\w$]++)|(?P<open>\()"
        r"|(?P<end>;)|(?P<bad>['\"`]|/\*)|(?P<other>.)",
        re.DOTALL,
    )


_LEAD: Final = re.compile(r"(?:\s|\(|/\*.*?\*/|--[^\n]*+)*+([A-Za-z]+)", re.DOTALL)
_TOKEN: Final = _lexer(r"'(?:[^']|'')*+'")
_ESCAPED: Final = _lexer(r"'(?:[^'\\]|''|\\.)*+'")
_LEXERS: Final = (_TOKEN, _ESCAPED)
"""The standard reading of a string and the one where a backslash escapes a quote.

A dialect may take either, so a statement is a read only if both readings agree.
"""
_GAP: Final = r"(?:\s|/\*.*?\*/|--[^\n]*+)*+"
_CALL: Final = re.compile(
    rf"(?:(\.{_GAP})?(?<![\w$])([\w$]+)|[\"`]){_GAP}\(", re.DOTALL
)
_HINT: Final = re.compile(
    r"\b(?:INTO|UPDATE|SHARE|INSERT|DELETE|MERGE)\b|;", re.IGNORECASE
)
_PRAGMA: Final = re.compile(
    rf"{_GAP}(?:[A-Za-z_]\w*{_GAP}\.{_GAP})?([A-Za-z_]\w*)", re.DOTALL
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
_EXPLAINS: Final = frozenset({"EXPLAIN", "DESCRIBE", "DESC"})
_ANALYZE: Final = frozenset({"ANALYZE", "ANALYSE"})
_EXPLAIN_OPTIONS: Final = frozenset(
    {
        "EXTENDED",
        "FORMAT",
        "JSON",
        "PARTITIONS",
        "TEXT",
        "TRADITIONAL",
        "TREE",
        "VERBOSE",
    }
)
_READ_TARGETS: Final = frozenset({"SELECT", "TABLE", "VALUES", "WITH"})
_SESSION_PRAGMAS: Final = frozenset(
    {
        "ANALYSIS_LIMIT",
        "AUTOMATIC_INDEX",
        "BUSY_TIMEOUT",
        "CACHE_SIZE",
        "CACHE_SPILL",
        "CASE_SENSITIVE_LIKE",
        "CELL_SIZE_CHECK",
        "CHECKPOINT_FULLFSYNC",
        "COLLATION_LIST",
        "COMPILE_OPTIONS",
        "DATA_VERSION",
        "DATABASE_LIST",
        "DEFER_FOREIGN_KEYS",
        "FOREIGN_KEY_CHECK",
        "FOREIGN_KEY_LIST",
        "FOREIGN_KEYS",
        "FREELIST_COUNT",
        "FULLFSYNC",
        "FUNCTION_LIST",
        "HARD_HEAP_LIMIT",
        "IGNORE_CHECK_CONSTRAINTS",
        "INDEX_INFO",
        "INDEX_LIST",
        "INDEX_XINFO",
        "INTEGRITY_CHECK",
        "JOURNAL_SIZE_LIMIT",
        "LEGACY_ALTER_TABLE",
        "LOCKING_MODE",
        "MAX_PAGE_COUNT",
        "MMAP_SIZE",
        "MODULE_LIST",
        "PAGE_COUNT",
        "PRAGMA_LIST",
        "QUERY_ONLY",
        "QUICK_CHECK",
        "READ_UNCOMMITTED",
        "RECURSIVE_TRIGGERS",
        "REVERSE_UNORDERED_SELECTS",
        "SECURE_DELETE",
        "SHRINK_MEMORY",
        "SOFT_HEAP_LIMIT",
        "SYNCHRONOUS",
        "TABLE_INFO",
        "TABLE_LIST",
        "TABLE_XINFO",
        "TEMP_STORE",
        "THREADS",
        "TRUSTED_SCHEMA",
        "WAL_AUTOCHECKPOINT",
    }
)
"""The pragmas that read or set the connection alone, every other one a write.

One outside them, `user_version` or `journal_mode` say, may change the database file.
"""
_PURE: Final = frozenset(
    {
        "ALL",
        "AND",
        "ANY",
        "ARRAY",
        "AS",
        "BETWEEN",
        "BY",
        "CASE",
        "ELSE",
        "EXCEPT",
        "EXISTS",
        "FILTER",
        "FROM",
        "HAVING",
        "IN",
        "INTERSECT",
        "IS",
        "JOIN",
        "LATERAL",
        "LIKE",
        "NOT",
        "ON",
        "OR",
        "OVER",
        "ROW",
        "SELECT",
        "SOME",
        "THEN",
        "UNION",
        "USING",
        "VALUES",
        "WHEN",
        "WHERE",
        "WITHIN",
        "BINARY",
        "BIT",
        "CHAR",
        "CHARACTER",
        "DATETIME",
        "DECIMAL",
        "DOUBLE",
        "FLOAT",
        "INTERVAL",
        "NCHAR",
        "NUMBER",
        "NUMERIC",
        "NVARCHAR",
        "NVARCHAR2",
        "RAW",
        "SIGNED",
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "UNSIGNED",
        "VARBINARY",
        "VARCHAR",
        "VARCHAR2",
        "ABS",
        "ACOS",
        "ANY_VALUE",
        "ARRAY_AGG",
        "ARRAY_LENGTH",
        "ASCII",
        "ASIN",
        "ATAN",
        "ATAN2",
        "AVG",
        "BITAND",
        "BITXOR",
        "BIT_AND",
        "BIT_OR",
        "BIT_XOR",
        "BOOL_AND",
        "BOOL_OR",
        "CARDINALITY",
        "CAST",
        "CEIL",
        "CEILING",
        "CHAR_LENGTH",
        "CHR",
        "COALESCE",
        "COLLATE",
        "CONCAT",
        "CONCAT_WS",
        "CONVERT",
        "CONVERT_TZ",
        "CORR",
        "COS",
        "COT",
        "COUNT",
        "COVAR_POP",
        "COVAR_SAMP",
        "CUME_DIST",
        "CURRENT_TIMESTAMP",
        "DATE",
        "DATE_FORMAT",
        "DATE_SUB",
        "DATE_TRUNC",
        "DAYOFWEEK",
        "DEGREES",
        "DENSE_RANK",
        "EXP",
        "EXTRACT",
        "FIRST_VALUE",
        "FLOOR",
        "GEN_RANDOM_UUID",
        "GREATEST",
        "GROUP_CONCAT",
        "HOST",
        "IFNULL",
        "INSTR",
        "JSON",
        "JSONB_AGG",
        "JSONB_BUILD_ARRAY",
        "JSONB_BUILD_OBJECT",
        "JSON_ARRAY",
        "JSON_CONTAINS",
        "JSON_CONTAINS_PATH",
        "JSON_EQUAL",
        "JSON_EXISTS",
        "JSON_EXTRACT",
        "JSON_OBJECT",
        "JSON_QUERY",
        "JSON_TYPE",
        "JSON_UNQUOTE",
        "JSON_VALUE",
        "LAG",
        "LAST_VALUE",
        "LEAD",
        "LEAST",
        "LEFT",
        "LENGTH",
        "LISTAGG",
        "LN",
        "LOG",
        "LOWER",
        "LOWER_INC",
        "LOWER_INF",
        "LPAD",
        "LTRIM",
        "MAKEDATE",
        "MAX",
        "MD5",
        "MICROSECOND",
        "MIN",
        "MOD",
        "NOW",
        "NTH_VALUE",
        "NTILE",
        "NULLIF",
        "NUMTODSINTERVAL",
        "ORD",
        "PERCENT_RANK",
        "PI",
        "PLAINTO_TSQUERY",
        "PHRASETO_TSQUERY",
        "POSITION",
        "POW",
        "POWER",
        "QUARTER",
        "QUOTE",
        "RADIANS",
        "RAND",
        "RANDOM",
        "RANK",
        "REGEXP",
        "REGEXP_LIKE",
        "REGR_AVGX",
        "REGR_AVGY",
        "REGR_COUNT",
        "REGR_INTERCEPT",
        "REGR_R2",
        "REGR_SLOPE",
        "REGR_SXX",
        "REGR_SXY",
        "REGR_SYY",
        "REPEAT",
        "REPLACE",
        "REVERSE",
        "RIGHT",
        "ROUND",
        "ROW_NUMBER",
        "RPAD",
        "RTRIM",
        "SETWEIGHT",
        "SHA1",
        "SHA224",
        "SHA256",
        "SHA384",
        "SHA512",
        "SIGN",
        "SIMILARITY",
        "SIN",
        "SQRT",
        "STATEMENT_TIMESTAMP",
        "STDDEV_POP",
        "STDDEV_SAMP",
        "STRFTIME",
        "STRICT_WORD_SIMILARITY",
        "STRING_AGG",
        "STRPOS",
        "SUBSTR",
        "SUBSTRING",
        "SUM",
        "TAN",
        "TIME",
        "TIMESTAMPDIFF",
        "TIME_TO_SEC",
        "TO_BLOB",
        "TO_CHAR",
        "TO_DATE",
        "TO_NCLOB",
        "TO_NUMBER",
        "TO_TIMESTAMP",
        "TO_TSQUERY",
        "TO_TSVECTOR",
        "TRIM",
        "TRUNC",
        "TRUNCATE",
        "TS_HEADLINE",
        "TS_RANK",
        "TS_RANK_CD",
        "UNACCENT",
        "UNICODE",
        "UNNEST",
        "UPPER",
        "UPPER_INC",
        "UPPER_INF",
        "UTC_TIMESTAMP",
        "UUID",
        "UUIDV4",
        "UUIDV7",
        "VAR_POP",
        "VAR_SAMP",
        "WEBSEARCH_TO_TSQUERY",
        "WEEK",
        "WEEKDAY",
        "WORD_SIMILARITY",
        "YEAR",
        "YEARWEEK",
        "DJANGO_DATE_EXTRACT",
        "DJANGO_DATE_TRUNC",
        "DJANGO_DATETIME_CAST_DATE",
        "DJANGO_DATETIME_CAST_TIME",
        "DJANGO_DATETIME_EXTRACT",
        "DJANGO_DATETIME_TRUNC",
        "DJANGO_FORMAT_DTDELTA",
        "DJANGO_TIME_DIFF",
        "DJANGO_TIME_EXTRACT",
        "DJANGO_TIME_TRUNC",
        "DJANGO_TIMESTAMP_DIFF",
    }
)
"""The keywords and built-in functions a read may call, every other call a write.

They are the ones Django's expressions and backends emit, none with a side effect.
"""


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


class _Scan(NamedTuple):
    """The bare words and the names called of one reading, and whether it is one."""

    words: tuple[str, ...]
    called: tuple[str, ...]
    single: bool


def _scan(text: str, start: int, lexer: re.Pattern[str]) -> _Scan | None:
    """Return one reading of the text after `start`, `None` if a quote never ends.

    A quoted or qualified name called is named `""`, which no read takes, and a token
    past a `;` makes the reading more than one statement.
    """
    words: list[str] = []
    called: list[str] = []
    last: str | None = None
    ended = dotted = False
    for token in lexer.finditer(text, start):
        group = token.lastgroup
        if group is None:
            continue
        if group == "bad":
            return None
        if ended:
            return _Scan(tuple(words), tuple(called), single=False)
        if group == "open" and last is not None:
            called.append(last)
        last = None
        if group == "word":
            word = token.group("word").upper()
            words.append(word)
            last = "" if dotted else word
        elif group == "name":
            last = ""
        elif group == "end":
            ended = True
        dotted = token.group() == "."
    return _Scan(tuple(words), tuple(called), single=True)


def _scans(text: str, start: int) -> list[_Scan]:
    """Return every reading of the text that closes its quotes, a backslash read too."""
    lexers = _LEXERS if "\\" in text else _LEXERS[:1]
    return [
        scanned
        for lexer in lexers
        if (scanned := _scan(text, start, lexer)) is not None
    ]


def _single(text: str, start: int) -> bool:
    """Whether every reading of the text after `start` holds one statement alone."""
    if text.find(";", start) < 0:
        return True
    scans = _scans(text, start)
    return bool(scans) and all(scanned.single for scanned in scans)


def _calls_beyond(text: str, start: int) -> bool:
    """Whether the text may call a function no read takes, judged before a full scan."""
    for match in _CALL.finditer(text, start):
        dot, name = match.groups()
        if name is None or dot is not None or name.upper() not in _PURE:
            return True
    return False


def _calls_nothing(text: str, start: int) -> bool:
    """Whether no reading of the text after `start` calls a function no read takes."""
    if "(" not in text or not _calls_beyond(text, start):
        return True
    scans = _scans(text, start)
    return bool(scans) and all(_PURE.issuperset(scanned.called) for scanned in scans)


def _changes(lead: str, words: tuple[str, ...]) -> bool:
    """Whether a read changes data or locks rows, judged by its bare words."""
    loose = lead in _LOOSE
    before = ""
    for word in words:
        if word in _CHANGES or (word in _LOCKED and (loose or before in _LOCKING)):
            return True
        before = word
    return False


def _analyzes_beyond_read(text: str, start: int) -> bool:
    """Whether an `EXPLAIN ANALYZE` runs a statement other than a read.

    The options before the explained statement are skipped, a parenthesised list whole.
    """
    analyzed = False
    depth = 0
    for token in _TOKEN.finditer(text, start):
        group = token.lastgroup
        if group is None:
            continue
        if group == "open":
            depth += 1
            continue
        value = token.group()
        if depth:
            depth -= value == ")"
            analyzed = analyzed or value.upper() in _ANALYZE
            continue
        word = value.upper() if group == "word" else None
        if word in _ANALYZE:
            analyzed = True
        elif word not in _EXPLAIN_OPTIONS and value != "=":
            return analyzed and word not in _READ_TARGETS
    return analyzed


def _read(text: str, start: int, lead: str) -> StatementKind:
    """Classify a read, one that changes data, locks rows or calls a function a write.

    No lexer knows what a function does, so a call outside `_PURE` counts as a write.
    """
    if lead in _EXPLAINS and _analyzes_beyond_read(text, start):
        return StatementKind.WRITE
    if _HINT.search(text, start) is None and not _calls_beyond(text, start):
        return StatementKind.READ
    scans = _scans(text, start)
    if not scans:
        return StatementKind.WRITE
    for scanned in scans:
        if (
            not scanned.single
            or _changes(lead, scanned.words)
            or not _PURE.issuperset(scanned.called)
        ):
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


def _pragma(text: str, start: int) -> StatementKind:
    """Classify a `PRAGMA` by its name, a schema before it skipped."""
    name = _PRAGMA.match(text, start)
    session = name is not None and name.group(1).upper() in _SESSION_PRAGMAS
    if session and _single(text, start):
        return StatementKind.SESSION
    return StatementKind.WRITE


def _unlisted(text: str, start: int, lead: str) -> StatementKind:
    """Classify a statement `_LEADS` does not list, by what follows its keyword."""
    if lead == "PRAGMA":
        return _pragma(text, start)
    if lead not in _FOLLOWED:
        return StatementKind.WRITE
    kinds = {
        _followed(lead, scanned.words) if scanned.single else StatementKind.WRITE
        for scanned in _scans(text, start)
    }
    return kinds.pop() if len(kinds) == 1 else StatementKind.WRITE


def statement_kind(sql: object) -> StatementKind:
    """Classify a statement by its leading keyword, an unknown one counting as a write.

    Comments, quoted text and quoted names never decide, so only real keywords count.
    A read or a `SET` that calls a function it does not know, or two statements, write.
    """
    text = _text(sql)
    if text is None:
        return StatementKind.WRITE
    lead = _LEAD.match(text)
    if lead is None:
        return StatementKind.WRITE
    word, start = lead.group(1).upper(), lead.end()
    kind = _LEADS.get(word)
    if kind is StatementKind.READ:
        return _read(text, start, word)
    if kind is not None:
        settled = _single(text, start) and (
            kind is not StatementKind.SESSION or _calls_nothing(text, start)
        )
        return kind if settled else StatementKind.WRITE
    return _unlisted(text, start, word)


def nests(kind: StatementKind, sql: object) -> bool:
    """Whether the statement opens a savepoint, which every later one nests inside."""
    if kind is not StatementKind.SESSION:
        return False
    lead = _LEAD.match(_text(sql) or "")
    return lead is not None and lead.group(1).upper() == "SAVEPOINT"


__all__ = ["StatementKind", "nests", "statement_kind"]
