"""HTTP test client extensions for next-dj."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from html import unescape
from typing import TYPE_CHECKING, Any, Final, cast, override

from django.apps import apps
from django.contrib.auth import get_user
from django.http import HttpRequest, QueryDict, StreamingHttpResponse
from django.test import AsyncClient, Client, RequestFactory
from django.urls import resolve

from next.boundaries.failures import ErrorKind
from next.boundaries.options import DeferMode
from next.components.trip import TripInput
from next.compression.streams import media_type
from next.defer.manager import defer_manager
from next.defer.reload import RETRY_WITH, page_components
from next.defer.signals import boundary_settled
from next.defer.tokens.carrier import FIELD
from next.defer.trip import mint, note_route
from next.forms.uid import ORIGIN_FIELD_NAME
from next.partial import keys
from next.partial.envelope import Envelope
from next.partial.headers import (
    BOUNDARY,
    LAYOUTS,
    NAVIGATE,
    ORIGIN,
    PREFETCH,
    REQUEST_FLAG,
    SEC_FETCH_SITE,
    VERSION,
    ZONE,
)
from next.partial.keys import BOUNDARY_ATTR, BOUNDARY_KEY_ATTR, CARRIER_ATTR, TRIP_ATTR
from next.partial.manager import partial_backend_manager
from next.router.markers import MAX_LAYOUTS, CutKind, Divergence
from next.router.navigation import DECLARATION_BYTES
from next.router.render import LAYOUTS_ATTR
from next.router.signals import navigation_rendered

from .actions import resolve_action_url
from .boundaries import HeldFailures, RecordedFailure, raise_first
from .capture import SignalRecorder, capture_signals
from .streaming import StreamedPage, aread_stream, decoded, names_of, read_stream


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from os import PathLike
    from pathlib import Path

    from django.http import HttpResponse, HttpResponseBase
    from django.test.client import (
        _MonkeyPatchedASGIResponse,
        _MonkeyPatchedWSGIResponse,
    )

    from next.components.info import ComponentInfo


_SESSIONS_APP = "django.contrib.sessions"
_AUTH_APP = "django.contrib.auth"
_ADDRESS = "b-00000000-0"
_SAME_ORIGIN = "same-origin"
_VISIT = "visit"
_LAYOUTS_BLOCK: Final = re.compile(
    rf"<script[^>]*\s{LAYOUTS_ATTR}[^>]*>(?P<chain>.*?)</script>"
)
_WRAPPER = re.compile(
    rf'\sid="(?P<id>b-[0-9a-f]+-[0-9]+)" {BOUNDARY_ATTR}="(?P=id)" '
    rf'{BOUNDARY_KEY_ATTR}="(?P<key>[^"]*)"[^>]*?\s{TRIP_ATTR}="(?P<token>[^"]*)"'
    rf'(?:\s{CARRIER_ATTR}="(?P<carrier>[^"]*)")?'
)


class PartialEnvelope:
    """Structural view over a decoded patch envelope for test assertions.

    The payload is rebuilt into the producer's envelope objects, not a second wire read.
    """

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        *,
        envelope: Envelope | None = None,
        response: HttpResponseBase | None = None,
    ) -> None:
        """Wrap a decoded envelope mapping, or the objects passed as `envelope`.

        `response` is the one that carried the envelope, `None` for a hand-built view.
        """
        self._data = data
        self._parsed = envelope
        self.response = response

    @classmethod
    def from_envelope(
        cls, envelope: Envelope, response: HttpResponseBase | None = None
    ) -> PartialEnvelope:
        """Wrap envelope objects directly, skipping the mapping round trip."""
        return cls(envelope=envelope, response=response)

    @property
    def data(self) -> dict[str, Any]:
        """Return the raw mapping, serialized once from the envelope when absent."""
        if self._data is None:
            self._data = self._envelope().as_dict()
        return self._data

    def _envelope(self) -> Envelope:
        """Return the envelope objects behind the mapping, built once per view.

        Deferred so an unread view costs nothing and a malformed one fails where read.
        """
        if self._parsed is None:
            self._parsed = Envelope.from_dict(self._data or {})
        return self._parsed

    def _addressed(self, selector: str) -> list[str]:
        """Return the selector value of each op naming it, a refresh's in its extras."""
        named: list[str] = []
        for patch in self._envelope().ops:
            if isinstance(patch.target, dict) and selector in patch.target:
                named.append(patch.target[selector])
            elif patch.op == "refresh" and selector in patch.extras:
                named.append(patch.extras[selector])
        return named

    @property
    def version(self) -> str:
        """Return the asset version stamped in the envelope."""
        return self._envelope().version

    @property
    def ops(self) -> list[dict[str, Any]]:
        """Return the ordered list of op objects."""
        return [patch.as_dict() for patch in self._envelope().ops]

    @property
    def assets(self) -> list[dict[str, Any]]:
        """Return the asset manifest entries."""
        return [asset.as_dict() for asset in self._envelope().assets]

    def op_verbs(self) -> list[str]:
        """Return the verb of every op in order."""
        return [patch.op for patch in self._envelope().ops]

    def targets(self) -> list[dict[str, Any] | None]:
        """Return the target object of every op in order."""
        return [
            None if patch.target is None else dict(patch.target)
            for patch in self._envelope().ops
        ]

    def zone_targets(self) -> list[str]:
        """Return the zone name of every op that addresses a zone, in order.

        A `refresh` names its zone on the op itself rather than in a target.
        """
        return self._addressed(keys.ZONE)

    def form_targets(self) -> list[str]:
        """Return the form uid of every op that addresses a form, in order."""
        return self._addressed(keys.FORM_SELECTOR)

    def form_meta(self) -> dict[str, Any] | None:
        """Return the machine-readable form meta object of the envelope."""
        form = self._envelope().form
        return None if form is None else form.as_dict()

    def toasts(self) -> list[dict[str, Any]]:
        """Return the payload of every toast op in order."""
        return [
            patch.as_dict() for patch in self._envelope().ops if patch.op == "toast"
        ]

    def html_for_zone(self, zone: str) -> str:
        """Return the HTML payload of the op morphing the named zone."""
        for patch in self._envelope().ops:
            target = patch.target
            if isinstance(target, dict) and target.get(keys.ZONE) == zone:
                return patch.html or ""
        msg = f"no op targets zone {zone!r}"
        raise AssertionError(msg)


@dataclass(frozen=True, slots=True)
class ReloadedBoundary:
    """One client-trip boundary a reload settled, read off the op that fills it.

    `retry` is the token a failed reload offers to try again with, else `None`.
    """

    id: str
    state: str
    kind: ErrorKind | None
    html: str
    retry: str | None
    envelope: PartialEnvelope

    @property
    def response(self) -> HttpResponseBase | None:
        """Return the response that carried the reload."""
        return self.envelope.response


def _reloaded(envelope: PartialEnvelope) -> ReloadedBoundary:
    """Return the boundary the first op of a reload envelope settles."""
    op = envelope.ops[0]
    kind = op.get(keys.CHUNK_KIND)
    return ReloadedBoundary(
        id=op[keys.TARGET][keys.BOUNDARY],
        state=op[keys.CHUNK_STATE],
        kind=None if kind is None else ErrorKind(kind),
        html=op.get(keys.HTML) or "",
        retry=op.get(RETRY_WITH),
        envelope=envelope,
    )


def _settle(response: HttpResponseBase, failures: tuple[RecordedFailure, ...]) -> None:
    """Hold the ordered failures of the complete response and re-raise the first."""
    vars(response)["boundary_failures"] = failures
    raise_first(failures)


def _consumed(
    chunks: Iterator[bytes], response: HttpResponseBase, held: HeldFailures
) -> Iterator[bytes]:
    """Yield `chunks`, recording what the render of each one contains.

    A stream closed before its end still hands on what its chunks contained so far.
    """
    writes: list[bytes] = []
    try:
        while True:
            with held.recording():
                chunk = next(chunks, None)
            if chunk is None:
                break
            writes.append(chunk)
            yield chunk
    finally:
        failures = held.settle(b"".join(decoded(response, writes)))
    _settle(response, failures)


async def _aconsumed(
    chunks: AsyncIterator[bytes], response: HttpResponseBase, held: HeldFailures
) -> AsyncIterator[bytes]:
    """Yield `chunks` of an async stream, recording what each render contains."""
    writes: list[bytes] = []
    try:
        while True:
            with held.recording():
                chunk = await anext(chunks, None)
            if chunk is None:
                break
            writes.append(chunk)
            yield chunk
    finally:
        failures = held.settle(b"".join(decoded(response, writes)))
    _settle(response, failures)


def envelope_of(response: HttpResponse) -> PartialEnvelope:
    """Return the structural envelope view of a partial response.

    A navigation fallback is no envelope and raises rather than passing the assertion.
    """
    backend = partial_backend_manager.get()
    content_type = response["Content-Type"].split(";")[0].strip()
    if content_type != backend.content_type:
        msg = f"response is not a patch envelope, content type is {content_type!r}"
        raise AssertionError(msg)
    envelope = backend.deserialize_envelope(response.content)
    return PartialEnvelope.from_envelope(envelope, response)


def _declaration(chain: Sequence[Sequence[object]]) -> str:
    """Join `id.digest` per layout, trimmed to the bounds the client router keeps."""
    declared = [f"{entry[0]}.{entry[1]}" for entry in chain[:MAX_LAYOUTS]]
    while len(",".join(declared)) > DECLARATION_BYTES:
        declared.pop()
    return ",".join(declared)


def declared_layouts(document: str) -> str:
    """Return the `X-Next-Layouts` value a browser showing `document` sends.

    It reads the layouts block of the document, which the router writes.
    """
    found = _LAYOUTS_BLOCK.search(document)
    if found is None:
        msg = "the document carries no layouts block, as the client router is off"
        raise LookupError(msg)
    return _declaration(json.loads(found["chain"]))


@dataclass(frozen=True, slots=True)
class Navigation:
    """One client router navigation, its envelope and the cut the page view chose.

    `kind` is `None` when the view cut nothing, as for a refused prefetch.
    """

    response: HttpResponseBase
    envelope: PartialEnvelope | None
    kind: CutKind | None
    kept: tuple[str, ...] = ()
    diverged: str | None = None
    reason: Divergence | None = None
    stream: StreamedPage | None = None

    @property
    def layouts(self) -> str:
        """Return the declaration the client sends on the navigation after this one."""
        chain = None if self.envelope is None else self.envelope.data.get(keys.LAYOUTS)
        return _declaration(chain) if isinstance(chain, list) else ""


def _read_envelope(
    response: HttpResponseBase, recorder: SignalRecorder
) -> tuple[PartialEnvelope | None, StreamedPage | None]:
    """Return the envelope of a navigation response and the stream it opened, if any."""
    if isinstance(response, StreamingHttpResponse):
        writes = list(cast("Iterator[bytes]", response.streaming_content))
        stream = StreamedPage(writes, response, names=names_of(recorder))
        parsed = stream.envelope
        envelope = (
            None if parsed is None else PartialEnvelope.from_envelope(parsed, response)
        )
        return envelope, stream
    backend = partial_backend_manager.get()
    if media_type(response.get("Content-Type", "")) != backend.content_type:
        return None, None
    return envelope_of(cast("HttpResponse", response)), None


def _navigation(response: HttpResponseBase, recorder: SignalRecorder) -> Navigation:
    """Return the navigation `response` answers, its cut read from the signal sent."""
    envelope, stream = _read_envelope(response, recorder)
    events = recorder.events_for(navigation_rendered)
    if events:
        sent = events[-1].kwargs
        return Navigation(
            response,
            envelope,
            sent["cut"],
            kept=sent["kept"],
            diverged=sent["diverged_at"],
            reason=sent["reason"],
            stream=stream,
        )
    visit = envelope is not None and _VISIT in envelope.op_verbs()
    return Navigation(response, envelope, CutKind.VISIT if visit else None)


def _document(response: HttpResponseBase) -> str:
    """Return the whole HTML of a document response, a streamed one read to its end."""
    if isinstance(response, StreamingHttpResponse):
        return b"".join(cast("Iterator[bytes]", response.streaming_content)).decode()
    return cast("HttpResponse", response).content.decode()


def _source_html(source: HttpResponseBase | PartialEnvelope) -> str:
    """Return the HTML a document holds, or that the ops of an envelope carry."""
    if isinstance(source, PartialEnvelope):
        envelope = source
    elif isinstance(source, StreamingHttpResponse) or (
        media_type(source.get("Content-Type", ""))
        != partial_backend_manager.get().content_type
    ):
        return _document(source)
    else:
        envelope = envelope_of(cast("HttpResponse", source))
    return "".join(op.get("html") or "" for op in envelope.ops)


def _held(response: HttpResponseBase, held: HeldFailures) -> None:
    """Hold the failures of `response`, its stream re-raising the first on its end."""
    if isinstance(response, StreamingHttpResponse):
        chunks = response.streaming_content
        if isinstance(chunks, AsyncIterator):
            response.streaming_content = _aconsumed(chunks, response, held)
        else:
            response.streaming_content = _consumed(chunks, response, held)
        return
    _settle(response, held.settle(getattr(response, "content", b"")))


def _action(
    action_name: str,
    data: dict[str, Any] | None,
    *,
    origin: str,
    page: str | PathLike[str] | None,
    partial: bool | str | tuple[str, ...],
    version: str | None,
    headers: dict[str, str] | None,
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Return the URL, the payload and the headers of an action POST from `origin`."""
    url = resolve_action_url(action_name, page=page)
    payload: dict[str, Any] = dict(data or {})
    payload.setdefault(ORIGIN_FIELD_NAME, origin)
    sent = dict(headers or {})
    if partial:
        sent[REQUEST_FLAG] = "1"
    if isinstance(partial, str):
        sent[ZONE] = partial
    elif isinstance(partial, tuple):
        sent[ZONE] = ",".join(partial)
    if version is not None:
        sent[VERSION] = version
    return url, payload, sent


def _zones(
    zones: str | tuple[str, ...], version: str | None, headers: dict[str, str] | None
) -> dict[str, str]:
    """Return the headers a partial request for the named zones sends."""
    sent = dict(headers or {})
    sent[REQUEST_FLAG] = "1"
    sent[ZONE] = zones if isinstance(zones, str) else ",".join(zones)
    if version is not None:
        sent[VERSION] = version
    return sent


class NextClient(Client):
    """Django test client with next-dj form-action shortcuts.

    `post_action` POSTs to an action name, `get_zones` GETs a URL as partial zones.
    """

    @override
    def request(self, **request: object) -> _MonkeyPatchedWSGIResponse:
        """Send the request, holding its contained failures on `boundary_failures`.

        The first in document order re-raises once the response or stream is complete.
        """
        held = HeldFailures()
        with held.recording():
            response = super().request(**request)
        _held(response, held)
        return response

    def post_action(
        self,
        action_name: str,
        data: dict[str, Any] | None = None,
        *,
        origin: str,
        page: str | PathLike[str] | None = None,
        partial: bool | str | tuple[str, ...] = False,
        version: str | None = None,
        headers: dict[str, str] | None = None,
        **extra: object,
    ) -> HttpResponse:
        """Resolve `action_name` and POST `data` from the `origin` page to its URL.

        `partial` takes True for a partial request or the zone names it asks for.
        """
        url, payload, sent = _action(
            action_name,
            data,
            origin=origin,
            page=page,
            partial=partial,
            version=version,
            headers=headers,
        )
        forwarded: dict[str, Any] = extra
        return cast(
            "HttpResponse", self.post(url, data=payload, headers=sent, **forwarded)
        )

    def get_action_url(
        self, action_name: str, *, page: str | PathLike[str] | None = None
    ) -> str:
        """Return the reverse URL for a registered form action, `page` picking one."""
        return resolve_action_url(action_name, page=page)

    def get_stream(self, url: str, **extra: object) -> StreamedPage:
        """GET `url` as a streamed page, read whole into its shell and chunks.

        The client runs a single-threaded worker, which the policy reads as a threaded
        one here, so the page streams as it would under a threaded server.
        """
        return read_stream(self, url, **extra)

    def boundary_token(self, url: str, name: str, **props: object) -> str:
        """Mint the token the page at `url` gives the component `name` with `props`.

        It is bound to the session and the user this client holds.
        """
        match = resolve(url.split("?", 1)[0])
        page_path = getattr(match.func, "next_page_path", None)
        if page_path is None:
            msg = f"{url} reaches no page view"
            raise LookupError(msg)
        request = RequestFactory().get(url)
        if apps.is_installed(_SESSIONS_APP):
            request.session = self.session
        if apps.is_installed(_AUTH_APP) and hasattr(request, "session"):
            request.user = get_user(request)
        note_route(request, match.kwargs)
        info = _component(page_path, name)
        return mint(request, TripInput(info, DeferMode.LOAD, props))

    def get_boundary(self, url: str, name: str, **props: object) -> ReloadedBoundary:
        """Mint a token for the component `name` and reload it, as the browser does.

        The token travels in the configured carrier, and the settled boundary returns.
        """
        token = self.boundary_token(url, name, **props)
        return _reloaded(envelope_of(self.reload_boundary(url, token)))

    def reload_boundary(self, url: str, token: str) -> HttpResponse:
        """Send `token` to `url` as a boundary reload and return the response as is.

        The token may come from another client, so a test reads the refusal it earns.
        """
        place = defer_manager.tokens().carrier.describe()
        return self._reload(url, _ADDRESS, token, place.place, place.name)

    def follow_boundary(
        self, source: HttpResponseBase | PartialEnvelope | ReloadedBoundary, key: str
    ) -> ReloadedBoundary:
        """Reload a client-trip boundary a document or an envelope holds, as a browser.

        `key` is the wire id, the stable key or the component name the token claims.
        """
        if isinstance(source, ReloadedBoundary):
            source = source.envelope
        response = source.response if isinstance(source, PartialEnvelope) else source
        request = getattr(response, "wsgi_request", None)
        if not isinstance(request, HttpRequest):
            msg = "follow_boundary reads a response a test client returned"
            raise TypeError(msg)
        wrappers = list(_WRAPPER.finditer(_source_html(source)))
        found = next(
            (one for one in wrappers if key in {one["id"], unescape(one["key"])}), None
        )
        if found is None:
            backend = defer_manager.tokens()
            found = next(
                (
                    one
                    for one in wrappers
                    if backend.open(request, unescape(one["token"])).component == key
                ),
                None,
            )
        if found is None:
            msg = f"the source holds no client-trip boundary {key!r}"
            raise LookupError(msg)
        place, _, name = unescape(found["carrier"] or "").partition(":")
        token = unescape(found["token"])
        url = request.get_full_path()
        return _reloaded(
            envelope_of(self._reload(url, found["id"], token, place, name))
        )

    def _reload(
        self, url: str, ident: str, token: str, place: str, name: str
    ) -> HttpResponse:
        """Send the boundary reload the runtime sends for the wrapper `ident`."""
        sent = {REQUEST_FLAG: "1", ZONE: ident}
        field = name or FIELD
        if place == "query":
            path, _, query = url.partition("?")
            fields = QueryDict(query, mutable=True)
            fields[field] = token
            response = self.get(f"{path}?{fields.urlencode()}", headers=sent)
        elif place == "body":
            response = self.post(url, data={field: token}, headers=sent)
        else:
            sent[BOUNDARY] = token
            response = self.get(url, headers=sent)
        return cast("HttpResponse", response)

    def navigate(
        self,
        path: str,
        *,
        origin: str | None = None,
        layouts: str | None = None,
        prefetch: bool = False,
        headers: dict[str, str] | None = None,
        **extra: object,
    ) -> Navigation:
        """GET `path` as the client router does on a link from the `origin` page.

        Without `layouts` the origin loads as a document and its layouts block declares.
        """
        if layouts is None:
            layouts = "" if origin is None else declared_layouts(self._shown(origin))
        sent = {
            REQUEST_FLAG: "1",
            NAVIGATE: "1",
            LAYOUTS: layouts,
            SEC_FETCH_SITE: _SAME_ORIGIN,
        }
        if origin is not None:
            sent[ORIGIN] = origin
        if prefetch:
            sent[PREFETCH] = "1"
        sent.update(headers or {})
        forwarded: dict[str, Any] = extra
        with capture_signals(navigation_rendered, boundary_settled) as recorder:
            response = self.get(path, headers=sent, **forwarded)
            return _navigation(response, recorder)

    def _shown(self, origin: str) -> str:
        """Return the document a browser on `origin` shows, loaded as a plain GET."""
        return _document(self.get(origin))

    def get_zones(
        self,
        url: str,
        zones: str | tuple[str, ...],
        *,
        version: str | None = None,
        headers: dict[str, str] | None = None,
        **extra: object,
    ) -> HttpResponse:
        """GET `url` as a partial request for the named zones.

        `zones` joins into `X-Next-Zone` and `version` sets the asset version header.
        """
        sent = _zones(zones, version, headers)
        forwarded: dict[str, Any] = extra
        return cast("HttpResponse", self.get(url, headers=sent, **forwarded))


class AsyncNextClient(AsyncClient):
    """Django's async test client with the next-dj shortcuts that read no document.

    Each request holds its contained failures and re-raises the first, as `NextClient`.
    """

    @override
    async def request(self, **request: object) -> _MonkeyPatchedASGIResponse:
        """Send the request, holding its contained failures on `boundary_failures`."""
        held = HeldFailures()
        with held.recording():
            response = await super().request(**request)
        _held(response, held)
        return response

    async def post_action(
        self,
        action_name: str,
        data: dict[str, Any] | None = None,
        *,
        origin: str,
        page: str | PathLike[str] | None = None,
        partial: bool | str | tuple[str, ...] = False,
        version: str | None = None,
        headers: dict[str, str] | None = None,
        **extra: object,
    ) -> HttpResponse:
        """Resolve `action_name` and POST `data` from the `origin` page to its URL."""
        url, payload, sent = _action(
            action_name,
            data,
            origin=origin,
            page=page,
            partial=partial,
            version=version,
            headers=headers,
        )
        forwarded: dict[str, Any] = extra
        response = await self.post(url, data=payload, headers=sent, **forwarded)
        return cast("HttpResponse", response)

    def get_action_url(
        self, action_name: str, *, page: str | PathLike[str] | None = None
    ) -> str:
        """Return the reverse URL for a registered form action, `page` picking one."""
        return resolve_action_url(action_name, page=page)

    async def get_stream(self, url: str, **extra: object) -> StreamedPage:
        """GET `url` as a streamed page, read whole into its shell and chunks."""
        return await aread_stream(self, url, **extra)

    async def get_zones(
        self,
        url: str,
        zones: str | tuple[str, ...],
        *,
        version: str | None = None,
        headers: dict[str, str] | None = None,
        **extra: object,
    ) -> HttpResponse:
        """GET `url` as a partial request for the named zones."""
        sent = _zones(zones, version, headers)
        forwarded: dict[str, Any] = extra
        response = await self.get(url, headers=sent, **forwarded)
        return cast("HttpResponse", response)


def _component(page_path: Path, name: str) -> ComponentInfo:
    """Return the component `name` a render of the page can call."""
    for info in page_components(page_path).values():
        if info.name == name:
            return info
    msg = f"no component named {name!r} is visible from {page_path}"
    raise LookupError(msg)


__all__ = [
    "AsyncNextClient",
    "Navigation",
    "NextClient",
    "PartialEnvelope",
    "ReloadedBoundary",
    "declared_layouts",
    "envelope_of",
]
