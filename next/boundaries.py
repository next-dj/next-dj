"""The inline error boundary, which contains a failed subtree behind its error UI."""

from __future__ import annotations

import logging
import re
import secrets
import threading
import traceback
from collections.abc import Callable, Hashable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, cast, override

from django.conf import settings
from django.core.exceptions import (
    BadRequest,
    ImproperlyConfigured,
    PermissionDenied,
    RequestAborted,
    SuspiciousOperation,
)
from django.dispatch import Signal
from django.http import Http404
from django.http.multipartparser import MultiPartParserError
from django.utils.html import escape
from django.utils.module_loading import import_string

from next.conf.defer import defer_option
from next.conf.settings import next_framework_settings
from next.defer import BoundaryError, DebugBoundaryError, ErrorKind
from next.diagnostics import INTENDED_EXCEPTIONS, Capture, FailureLog, mark_degraded
from next.transactions import (
    AtomicMode,
    LazySavepoint,
    lazy_savepoint,
    probe_atomic_aliases,
)


if TYPE_CHECKING:
    from types import TracebackType

    from django.http import HttpRequest

    from next.static.collector import StaticCollector


logger = logging.getLogger("next.boundaries")

_failures = FailureLog(logger)

boundary_failed: Signal = Signal(use_caching=True)
"""Sent once per failure a boundary contained, with the `contain` function as sender."""

_KEY: Final = "ERRORS"
_REPORTER_SETTING: Final = "NEXT_FRAMEWORK['DEFER']['ERRORS']['REPORTER']"
_BAD_REQUESTS: Final = (BadRequest, SuspiciousOperation, MultiPartParserError)
_SECURITY_LOGGED: Final = (SuspiciousOperation, MultiPartParserError)
_DIGEST_BYTES: Final = 6
_SEAL_BYTES: Final = 4
_RETRY_BUNDLE: Final = "defer"

RETRY_ATTR: Final = "data-next-retry"
"""The attribute of a retry control in an error UI, which the `defer` bundle answers."""

_RETRY_CONTROL: Final = re.compile(
    r"<[a-z][^\s/>]*"
    r"(?:\s+[^\s\"'=<>/]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'>]+))?)*?"
    rf"\s+{RETRY_ATTR}(?=[\s=/>])",
    re.IGNORECASE,
)

ADOPTING_ELEMENTS: Final = frozenset(
    {
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "ul",
        "ol",
        "dl",
        "select",
        "optgroup",
        "colgroup",
    }
)
"""The elements no wrapper element may sit directly inside, `svg` holding any depth."""

_FOREIGN: Final = "svg"
_HTML_INSIDE_FOREIGN: Final = "foreignobject"

_VOID: Final = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)

_DETAIL_HOSTS: Final = frozenset(
    {
        "article",
        "aside",
        "blockquote",
        "dd",
        "dialog",
        "div",
        "fieldset",
        "figure",
        "footer",
        "header",
        "li",
        "main",
        "nav",
        "section",
        "td",
        "th",
    }
)
"""The wrappers a `<details>` child stays inside, where the HTML parser moves none."""

_MARKUP: Final = re.compile(
    r"<!--.*?(?:-->|\Z)"
    r"|<(?P<raw>script|style|textarea|title)\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>"
    r".*?(?:</(?P=raw)\s*>|\Z)"
    r"|<(?P<close>/?)(?P<name>[A-Za-z][^\s/>]*)"
    r"(?P<attrs>(?:[^>\"']|\"[^\"]*\"|'[^']*')*)>",
    re.DOTALL | re.IGNORECASE,
)

_LOGGED: Final = (
    "The %s %r failed and rendered its error.djx in place of its subtree, digest %s."
)
_REPORTER_FAILED: Final = (
    "%s %r raised, so the default digest and logging stand in for it."
)
_NO_DIGEST: Final = (
    "%s %r answered %r rather than a digest string, so the default digest and "
    "logging stand in for it."
)
_NOT_ONE_ROOT: Final = (
    "The error UI of %s renders %s where its boundary adopts the root element, so "
    "an empty template element carries the boundary instead."
)


@dataclass(frozen=True, slots=True)
class Boundary:
    """One error boundary as a render meets it, named as `boundary_failed` names it.

    `owner` tells boundaries of one name apart, and `ident` is the id of a wrapper drawn
    around it, which its failure reports instead of a fresh one.
    """

    name: str
    addressing: str
    request: HttpRequest | None = None
    collector: StaticCollector | None = None
    started: bool = False
    owner: Hashable | None = None
    site: str = ""
    source: str | None = None
    ident: str | None = None


class Fallback(Protocol):
    """What a boundary shows in place of a failed subtree, asked after the failure."""

    def retry(self) -> bool:
        """Return whether a `data-next-retry` inside the error UI finds a target."""

    def retry_token(self, error: BoundaryError) -> str:
        """Return the token attributes the failed wrapper retries with, else nothing."""

    def tag(self) -> str | None:
        """Return the wrapper element, `None` where no element may wrap the error UI."""

    def render(self, error: BoundaryError, kind: ErrorKind) -> str:
        """Render the error UI, `kind` being the kind before `KINDS` hides it."""


@dataclass(frozen=True, slots=True)
class Trace:
    """The exception line and the formatted traceback of one contained failure."""

    line: str
    text: str

    @classmethod
    def of(cls, exc: BaseException) -> Trace:
        """Return the trace of `exc`, formatted once for every reader."""
        line = "".join(traceback.format_exception_only(exc)).strip()
        return cls(line, "".join(traceback.format_exception(exc)))


@dataclass(frozen=True, slots=True)
class _Errors:
    """The `DEFER["ERRORS"]` values of one scope, a misfit read as its default."""

    reraise: bool
    reporter: str | None
    atomic: AtomicMode
    opaque: bool
    detail: bool


def _read_errors() -> _Errors:
    return _Errors(
        reraise=cast("bool", defer_option(_KEY, "RAISE")),
        reporter=cast("str | None", defer_option(_KEY, "REPORTER")),
        atomic=cast("AtomicMode", defer_option(_KEY, "ATOMIC")),
        opaque=defer_option(_KEY, "KINDS") == "opaque",
        detail=cast("bool", defer_option(_KEY, "DEBUG_DETAIL")),
    )


type Reporter = Callable[..., object]


class _ErrorsMemo:
    """Hold the values read for one `DEFER` mapping, dropped when it is replaced."""

    __slots__ = ("held", "reporter")

    def __init__(self) -> None:
        """Start empty, the first read fills it."""
        self.held: tuple[object, _Errors] | None = None
        self.reporter: tuple[str, Reporter] | None = None

    def get(self) -> _Errors:
        """Return the values of the current `DEFER` mapping."""
        scope = next_framework_settings.DEFER
        held = self.held
        if held is not None and held[0] is scope:
            return held[1]
        errors = _read_errors()
        self.held = (scope, errors)
        return errors

    def report(self, dotted: str) -> Reporter:
        """Return the `REPORTER` callable at `dotted`, imported once per path."""
        held = self.reporter
        if held is not None and held[0] == dotted:
            return held[1]
        try:
            target = import_string(dotted)
        except ImportError as exc:
            msg = f"{_REPORTER_SETTING} {dotted!r} could not be imported: {exc}."
            raise ImproperlyConfigured(msg) from exc
        if not callable(target):
            msg = f"{_REPORTER_SETTING} {dotted!r} names no callable."
            raise ImproperlyConfigured(msg)
        self.reporter = (dotted, target)
        return cast("Reporter", target)


_errors = _ErrorsMemo()


def contained(exc: BaseException, *, started: bool = False) -> bool:
    """Return whether a boundary contains `exc` rather than letting it propagate.

    Before the first byte Django answers the intended exceptions with their own status.
    """
    if not isinstance(exc, Exception) or isinstance(exc, RequestAborted):
        return False
    return started or not isinstance(exc, INTENDED_EXCEPTIONS)


def new_digest() -> str:
    """Return a fresh digest, which ties an error UI to its line in the log."""
    return secrets.token_hex(_DIGEST_BYTES)


def kind_of(exc: BaseException) -> ErrorKind:
    """Return the kind a contained exception settles a boundary as."""
    if isinstance(exc, Http404):
        return ErrorKind.NOT_FOUND
    if isinstance(exc, PermissionDenied):
        return ErrorKind.FORBIDDEN
    if isinstance(exc, _BAD_REQUESTS):
        return ErrorKind.BAD_REQUEST
    return ErrorKind.ERROR


def shown_kind(kind: ErrorKind, *, opaque: bool) -> ErrorKind:
    """Return the kind the error UI reads, every kind with a status reading `error`."""
    if opaque and kind.status is not None:
        return ErrorKind.ERROR
    return kind


def _self_closing(attrs: str) -> bool:
    return attrs.rstrip().endswith("/")


def _open_elements(text: str) -> list[str]:
    """Return the elements `text` leaves open, outermost first, lowercased."""
    stack: list[str] = []
    for match in _MARKUP.finditer(text):
        name = match.group("name")
        if name is None:
            continue
        lowered = name.lower()
        if match.group("close"):
            if lowered in stack:
                del stack[len(stack) - 1 - stack[::-1].index(lowered) :]
        elif not _self_closing(match.group("attrs")) and lowered not in _VOID:
            stack.append(lowered)
    return stack


def wrapper_tag(preceding: str, tag: str = "div") -> str | None:
    """Return `tag`, or `None` when the markup before a site leaves no valid wrapper.

    Inside a table, a list, a select or an `svg` the boundary adopts its root instead.
    """
    stack = _open_elements(preceding)
    for name in reversed(stack):
        if name == _HTML_INSIDE_FOREIGN:
            break
        if name == _FOREIGN:
            return None
    if stack and stack[-1] in ADOPTING_ELEMENTS:
        return None
    return tag


def _roots(body: str) -> tuple[list[int], bool]:
    """Return where the name of each top-level element ends and whether text is loose.

    Loose text is text outside every element, which no root element can carry.
    """
    ends: list[int] = []
    depth = 0
    loose = False
    position = 0
    for match in _MARKUP.finditer(body):
        if depth == 0 and body[position : match.start()].strip():
            loose = True
        position = match.end()
        raw = match.group("raw")
        name = match.group("name")
        if raw is not None:
            if depth == 0:
                ends.append(match.start() + 1 + len(raw))
        elif name is None:
            continue
        elif match.group("close"):
            loose = loose or depth == 0
            depth = max(depth - 1, 0)
        else:
            if depth == 0:
                ends.append(match.end("name"))
            if not _self_closing(match.group("attrs")) and name.lower() not in _VOID:
                depth += 1
    if depth == 0 and body[position:].strip():
        loose = True
    return ends, loose


def _missed(ends: list[int], *, loose: bool) -> str | None:
    """Return how the roots `_roots` found miss a single element, `None` for one."""
    if loose:
        return "loose text"
    if len(ends) == 1:
        return None
    return f"{len(ends)} root elements"


def misrooted(body: str) -> str | None:
    """Return how `body` misses a single root element, `None` when it has one."""
    ends, loose = _roots(body)
    return _missed(ends, loose=loose)


def debug_detail(trace: Trace) -> str:
    """Return the unstyled element that shows the exception line and its traceback."""
    return (
        f"<details open data-next-error-detail><summary>{escape(trace.line)}</summary>"
        f"<pre>{escape(trace.text)}</pre></details>"
    )


def _detail_attribute(text: str) -> str:
    return f' data-next-error-detail="{escape(text)}"'


@dataclass(frozen=True, slots=True)
class _Shown:
    """What the wrapper of one failure shows beside the error UI."""

    trace: Trace | None
    site: str
    debug: bool


_PLAIN: Final = _Shown(None, "a boundary", debug=False)


def _adopted(attributes: str, body: str, shown: _Shown) -> str:
    """Return `body` with `attributes` on its one root, else an empty placeholder.

    The detail is an attribute, since no element is valid beside a table row.
    """
    detail = "" if shown.trace is None else shown.trace.text
    ends, loose = _roots(body)
    found = _missed(ends, loose=loose)
    if found is None:
        end = ends[0]
        extra = _detail_attribute(detail) if detail else ""
        return f"{body[:end]}{attributes}{extra}{body[end:]}"
    _failures.warn(("roots", shown.site), _NOT_ONE_ROOT, shown.site, found)
    if shown.debug:
        message = _NOT_ONE_ROOT % (shown.site, found)
        detail = f"{message}\n\n{detail}" if detail else message
    extra = _detail_attribute(detail) if detail else ""
    return f"<template{attributes}{extra}></template>"


def _wrap(tag: str | None, attributes: str, body: str, shown: _Shown) -> str:
    if tag is None:
        return _adopted(attributes, body, shown)
    trace = shown.trace
    if trace is not None:
        if tag in _DETAIL_HOSTS:
            body += debug_detail(trace)
        else:
            attributes += _detail_attribute(trace.text)
    return f"<{tag}{attributes}>{body}</{tag}>"


def identity(ident: str) -> str:
    """Return the `id` and `data-next-boundary` attributes a wrapper is swapped by."""
    shown = escape(ident)
    return f' id="{shown}" data-next-boundary="{shown}"'


def _failure(kind: ErrorKind, digest: str | None) -> str:
    shown = "" if digest is None else f' data-next-error-digest="{escape(digest)}"'
    return f' data-next-state="error" data-next-error="{escape(kind.value)}"{shown}'


def _attributes(
    ident: str, kind: ErrorKind, digest: str | None, source: str | None = None
) -> str:
    named = "" if source is None else f' data-next-boundary-source="{escape(source)}"'
    return identity(ident) + _failure(kind, digest) + named


def adopt(attributes: str, body: str, *, site: str) -> str:
    """Return `body` with `attributes` on its one root, else an empty placeholder.

    `site` names the boundary in the warning a body without one root logs.
    """
    return _adopted(attributes, body, _Shown(None, site, debug=bool(settings.DEBUG)))


def failed(
    tag: str | None, attributes: str, error: BoundaryError, body: str, *, site: str
) -> str:
    """Return the error UI `body` in a failed wrapper, marked as an inline one is.

    The digest and the traceback detail show for a `DebugBoundaryError` alone.
    """
    debug = isinstance(error, DebugBoundaryError)
    trace = None
    if isinstance(error, DebugBoundaryError) and error.traceback:
        trace = Trace(error.message, error.traceback) if _errors.get().detail else None
    marked = attributes + _failure(error.kind, error.digest if debug else None)
    return _wrap(tag, marked, body, _Shown(trace, site, debug=debug))


def wrapped(
    tag: str | None,
    ident: str,
    kind: ErrorKind,
    body: str,
    digest: str | None = None,
    source: str | None = None,
) -> str:
    """Return `body` inside the wrapper of a boundary that settled as an error.

    A `None` tag stamps the wrapper attributes on the single root element of `body`.
    """
    return _wrap(tag, _attributes(ident, kind, digest, source), body, _PLAIN)


class _Render:
    """What every boundary of one top-level render on one thread shares.

    The first boundary probes the aliases and keeps the wrappers on for its siblings.
    """

    __slots__ = ("aliases", "count", "errors", "failing", "holder", "seal", "thread")

    def __init__(self) -> None:
        """Start before any boundary of the render."""
        self.thread = threading.get_ident()
        self.errors: _Errors | None = None
        self.aliases: tuple[str, ...] = ()
        self.holder: LazySavepoint | None = None
        self.failing: set[Hashable] = set()
        self.seal = ""
        self.count = 0

    def settings(self) -> _Errors:
        """Return the `ERRORS` values of the render, read once with the alias probe."""
        errors = self.errors
        if errors is None:
            errors = self.errors = _errors.get()
            self.aliases = probe_atomic_aliases()
            holder = lazy_savepoint(atomic=False)
            holder.__enter__()
            self.holder = holder
        return errors

    def next_id(self) -> str:
        """Return a boundary id unique within the response, from a bound minter."""
        minter = _minter.get()
        minted = None if minter is None else minter()
        if minted is not None:
            return minted
        if not self.seal:
            self.seal = secrets.token_hex(_SEAL_BYTES)
        self.count += 1
        return f"b-{self.seal}-{self.count}"

    def close(self) -> None:
        """Release the handle the first boundary opened, removing the wrappers."""
        holder = self.holder
        if holder is not None:
            self.holder = None
            holder.__exit__(None, None, None)


_render: ContextVar[_Render | None] = ContextVar("next_boundary_render", default=None)
_minter: ContextVar[Callable[[], str | None] | None] = ContextVar(
    "next_boundary_minter", default=None
)
_wrapper: ContextVar[str | None] = ContextVar("next_boundary_wrapper", default=None)


@contextmanager
def wrapping(ident: str) -> Iterator[None]:
    """Hand the id of the wrapper drawn around the block to the boundary claiming it."""
    token = _wrapper.set(ident)
    try:
        yield
    finally:
        _wrapper.reset(token)


def claim_wrapper() -> str | None:
    """Return the id `wrapping` bound and clear it, so a nested boundary mints anew."""
    ident = _wrapper.get()
    if ident is not None:
        _wrapper.set(None)
    return ident


@contextmanager
def minting(minter: Callable[[], str | None]) -> Iterator[None]:
    """Take the id of every error wrapper inside the block from `minter`, if it has one.

    A deferred render binds its session, so every wrapper shares one id scheme.
    """
    token = _minter.set(minter)
    try:
        yield
    finally:
        _minter.reset(token)


class _Scope(AbstractContextManager[None]):
    """The block one top-level render shares its boundary state in."""

    __slots__ = ("_token", "state")

    _token: Token[_Render | None]

    def __init__(self) -> None:
        """Start the state of a render no boundary met yet."""
        self.state = _Render()

    @override
    def __enter__(self) -> None:
        """Make the state the one every boundary of the block shares."""
        self._token = _render.set(self.state)

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Restore the state outside the block and release what it opened."""
        _render.reset(self._token)
        self.state.close()


_NESTED: Final[AbstractContextManager[None]] = nullcontext()


def _current() -> _Render | None:
    """Return the state of the render under way on this thread, if any."""
    state = _render.get()
    if state is not None and state.thread == threading.get_ident():
        return state
    return None


def render_boundaries() -> AbstractContextManager[None]:
    """Return the block every boundary of one top-level render shares its state in.

    An entry point opens it around its render, and a boundary outside one opens its own.
    """
    return _NESTED if _current() is not None else _Scope()


class _Protection:
    """The savepoint and the collector mark of one guarded subtree, in that order."""

    __slots__ = ("_collector", "_mark", "_savepoint")

    def __init__(
        self, savepoint: LazySavepoint, collector: StaticCollector | None, mark: int
    ) -> None:
        """Hold what `open` took."""
        self._savepoint = savepoint
        self._collector = collector
        self._mark = mark

    @classmethod
    def open(
        cls,
        aliases: tuple[str, ...],
        atomic: AtomicMode,
        collector: StaticCollector | None,
    ) -> _Protection:
        """Open the lazy savepoint, then mark the collector journal."""
        savepoint = lazy_savepoint(aliases, atomic=atomic)
        savepoint.__enter__()
        mark = 0 if collector is None else collector.mark()
        return cls(savepoint, collector, mark)

    def keep(self) -> None:
        """Release the savepoint, then the mark, keeping what the subtree did."""
        try:
            self._savepoint.__exit__(None, None, None)
        finally:
            if self._collector is not None:
                self._collector.release()

    def undo(self) -> bool:
        """Roll back the savepoint, then the journal, telling whether the undo held.

        A subtree whose transaction failed before its savepoint leaves it to the next.
        """
        try:
            return self._savepoint.rollback()
        finally:
            if self._collector is not None:
                self._collector.rollback(self._mark)


def protect(collector: StaticCollector | None = None) -> _Protection:
    """Open the savepoint and the journal mark a boundary guards non-template work with.

    The mode is the `ATOMIC` value `contain` reads, and the caller then keeps or undoes.
    """
    state = _current()
    if state is None:
        errors = _errors.get()
        return _Protection.open(probe_atomic_aliases(), errors.atomic, collector)
    atomic = state.settings().atomic
    aliases = probe_atomic_aliases() if atomic is False else state.aliases
    return _Protection.open(aliases, atomic, collector)


def contain(boundary: Boundary, render: Callable[[], str], fallback: Fallback) -> str:
    """Return what `render` returns, or the error UI of `fallback` when it fails.

    Outside `render_boundaries` the boundary opens the per-render state for itself.
    """
    state = _current()
    if state is not None:
        return _guard(state, boundary, render, fallback)
    scope = _Scope()
    with scope:
        return _guard(scope.state, boundary, render, fallback)


def _owner(boundary: Boundary) -> Hashable:
    owner = boundary.owner
    return (boundary.addressing, boundary.name) if owner is None else owner


def _guard(
    state: _Render, boundary: Boundary, render: Callable[[], str], fallback: Fallback
) -> str:
    failing = state.failing
    if failing and _owner(boundary) in failing:
        return render()
    errors = state.settings()
    atomic = errors.atomic
    aliases = probe_atomic_aliases() if atomic is False else state.aliases
    protection = _Protection.open(aliases, atomic, boundary.collector)
    try:
        html = render()
    except Exception as exc:
        undone = protection.undo()
        if errors.reraise or not undone or not contained(exc, started=boundary.started):
            raise
        return _settle(state, boundary, exc, fallback, errors)
    except BaseException:
        protection.undo()
        raise
    protection.keep()
    return html


def _settle(
    state: _Render,
    boundary: Boundary,
    exc: Exception,
    fallback: Fallback,
    errors: _Errors,
) -> str:
    """Report a contained failure, then render the error UI outside the guard."""
    mark_degraded()
    kind = kind_of(exc)
    if boundary.started and isinstance(exc, _SECURITY_LOGGED):
        logging.getLogger(f"django.security.{type(exc).__name__}").error(
            str(exc),
            exc_info=exc,
            extra={"status_code": 400, "request": boundary.request},
        )
    digest = _report(boundary, exc, kind, errors)
    ident = state.next_id() if boundary.ident is None else boundary.ident
    if boundary_failed.has_listeners(contain):
        boundary_failed.send_robust(
            sender=contain,
            id=ident,
            name=boundary.name,
            addressing=boundary.addressing,
            kind=kind,
            digest=digest,
            exception=exc,
            request=boundary.request,
        )
    shown = shown_kind(kind, opaque=errors.opaque)
    debug = bool(settings.DEBUG)
    trace = Trace.of(exc) if debug else None
    error = BoundaryError.of(
        shown,
        digest,
        retry=fallback.retry(),
        message="" if trace is None else trace.line,
        traceback="" if trace is None else trace.text,
    )
    owner = _owner(boundary)
    state.failing.add(owner)
    try:
        body = fallback.render(error, kind)
    finally:
        state.failing.discard(owner)
    site = boundary.site or f"the {boundary.addressing} {boundary.name!r}"
    attributes = _attributes(
        ident, shown, digest if debug else None, boundary.source if debug else None
    )
    token = fallback.retry_token(error)
    note_retry(boundary.collector, body, token)
    return _wrap(
        fallback.tag(),
        attributes + token,
        body,
        _Shown(trace if errors.detail else None, site, debug=debug),
    )


def note_retry(collector: StaticCollector | None, body: str, token: str) -> None:
    """Record the `defer` bundle an error UI offering a retry needs.

    A retry token on its wrapper or a start tag carrying the retry attribute offers one.
    """
    if collector is not None and (token or _offers_retry(body)):
        collector.require(_RETRY_BUNDLE)


def _offers_retry(body: str) -> bool:
    """Whether a start tag of `body` carries the retry attribute, text naming it not."""
    return _RETRY_CONTROL.search(body) is not None


def _reported(
    dotted: str, boundary: Boundary, exc: Exception, kind: ErrorKind
) -> str | None:
    """Return the digest `REPORTER` answers, `None` when it fails or has no reference.

    A digest means the reporter owns the failure, so the framework logs nothing of it.
    """
    capture = Capture()
    with capture:
        reporter = _errors.report(dotted)
        answer = reporter(
            exc,
            name=boundary.name,
            addressing=boundary.addressing,
            kind=kind,
            request=boundary.request,
        )
    if capture.error is not None:
        _failures.emit(
            logging.ERROR,
            ("reporter", dotted),
            _REPORTER_FAILED,
            (_REPORTER_SETTING, dotted),
            capture.error,
        )
        return None
    if answer is None:
        return None
    if isinstance(answer, str) and answer:
        return answer
    _failures.emit(
        logging.ERROR,
        ("digest", dotted),
        _NO_DIGEST,
        (_REPORTER_SETTING, dotted, answer),
    )
    return None


def _report(
    boundary: Boundary, exc: Exception, kind: ErrorKind, errors: _Errors
) -> str:
    """Hand the failure to `REPORTER`, else log it, and return its digest.

    Inside the quiet period the failure is logged at `DEBUG`, so every digest is found.
    """
    if errors.reporter is not None:
        answered = _reported(errors.reporter, boundary, exc, kind)
        if answered is not None:
            return answered
    digest = new_digest()
    _failures.emit(
        logging.ERROR,
        (boundary.addressing, boundary.name, type(exc).__qualname__),
        _LOGGED,
        (boundary.addressing, boundary.name, digest),
        exc,
        extra={"digest": digest},
        quiet=logging.DEBUG,
    )
    return digest


__all__ = [
    "ADOPTING_ELEMENTS",
    "RETRY_ATTR",
    "Boundary",
    "Fallback",
    "Trace",
    "adopt",
    "boundary_failed",
    "claim_wrapper",
    "contain",
    "contained",
    "debug_detail",
    "failed",
    "identity",
    "kind_of",
    "minting",
    "misrooted",
    "new_digest",
    "note_retry",
    "protect",
    "render_boundaries",
    "shown_kind",
    "wrapped",
    "wrapper_tag",
    "wrapping",
]
