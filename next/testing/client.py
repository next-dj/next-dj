"""HTTP test client extensions for next-dj."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from html import unescape
from typing import TYPE_CHECKING, Any, cast, override

from django.apps import apps
from django.contrib.auth import get_user
from django.http import HttpRequest, QueryDict, StreamingHttpResponse
from django.test import Client, RequestFactory
from django.urls import resolve

from next.components.trip import TripInput
from next.compression.streams import media_type
from next.conf.router import router_settings
from next.defer import DeferMode
from next.forms.uid import ORIGIN_FIELD_NAME
from next.partial import keys
from next.partial.envelope import Envelope
from next.partial.headers import (
    LAYOUTS,
    NAVIGATE,
    ORIGIN,
    PREFETCH,
    REQUEST_FLAG,
    SEC_FETCH_SITE,
    VERSION,
    ZONE,
)
from next.partial.manager import partial_backend_manager
from next.router.markers import CutKind, Divergence
from next.router.navigation import DECLARATION_BYTES
from next.router.signals import navigation_rendered
from next.static.runtime import LAYOUTS_PAYLOAD_KEY
from next.suspense.manager import suspense_manager
from next.suspense.reload import page_components
from next.suspense.tokens.transport import FIELD, HEADER
from next.suspense.trip import mint, note_route

from .actions import resolve_action_url
from .boundaries import HeldFailures, RecordedFailure, raise_first
from .capture import SignalEvent, capture_signals
from .html import init_payload
from .streaming import StreamedPage, decoded, read_stream


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from os import PathLike
    from pathlib import Path

    from django.http import HttpResponse, HttpResponseBase
    from django.test.client import _MonkeyPatchedWSGIResponse

    from next.components.info import ComponentInfo


_SESSIONS_APP = "django.contrib.sessions"
_AUTH_APP = "django.contrib.auth"
_ADDRESS = "b-00000000-0"
_SAME_ORIGIN = "same-origin"
_VISIT = "visit"
_WRAPPER = re.compile(
    r'\sid="(?P<id>b-[0-9a-f]+-[0-9]+)" data-next-boundary="(?P=id)" '
    r'data-next-boundary-key="(?P<key>[^"]*)"[^>]*?\sdata-next-token="(?P<token>[^"]*)"'
    r'(?:\sdata-next-transport="(?P<transport>[^"]*)")?'
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
    declared = [
        f"{entry[0]}.{entry[1]}" for entry in chain[: router_settings().max_layouts]
    ]
    while len(",".join(declared)) > DECLARATION_BYTES:
        declared.pop()
    return ",".join(declared)


def declared_layouts(document: str) -> str:
    """Return the `X-Next-Layouts` value a browser showing `document` sends.

    It reads the `$layouts` chain of the bootstrap payload, which the router writes.
    """
    payload = init_payload(document)
    if LAYOUTS_PAYLOAD_KEY not in payload:
        msg = "the document carries no $layouts, as the client router is off for it"
        raise LookupError(msg)
    return _declaration(payload[LAYOUTS_PAYLOAD_KEY])


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
    response: HttpResponseBase,
) -> tuple[PartialEnvelope | None, StreamedPage | None]:
    """Return the envelope of a navigation response and the stream it opened, if any."""
    if isinstance(response, StreamingHttpResponse):
        writes = list(cast("Iterator[bytes]", response.streaming_content))
        stream = StreamedPage(writes, response)
        parsed = stream.envelope
        envelope = (
            None if parsed is None else PartialEnvelope.from_envelope(parsed, response)
        )
        return envelope, stream
    backend = partial_backend_manager.get()
    if media_type(response.get("Content-Type", "")) != backend.content_type:
        return None, None
    return envelope_of(cast("HttpResponse", response)), None


def _navigation(response: HttpResponseBase, events: list[SignalEvent]) -> Navigation:
    """Return the navigation `response` answers, its cut read from the signal sent."""
    envelope, stream = _read_envelope(response)
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
        sent: HttpResponseBase = response
        if isinstance(sent, StreamingHttpResponse):
            chunks = sent.streaming_content
            if isinstance(chunks, AsyncIterator):
                sent.streaming_content = _aconsumed(chunks, sent, held)
            else:
                sent.streaming_content = _consumed(chunks, sent, held)
            return response
        _settle(response, held.settle(getattr(response, "content", b"")))
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

    def get_boundary(self, url: str, name: str, **props: object) -> PartialEnvelope:
        """Mint a token for the component `name` and reload it, as the browser does.

        The token travels through the configured transport, and the envelope returns.
        """
        token = self.boundary_token(url, name, **props)
        place = suspense_manager.tokens().transport.describe()
        return self._reload(url, _ADDRESS, token, place.place, place.name)

    def follow_boundary(
        self, source: HttpResponseBase | PartialEnvelope, key: str
    ) -> PartialEnvelope:
        """Reload a client-trip boundary a document or an envelope holds, as a browser.

        `key` is the wire id, the stable key or the component name the token claims.
        """
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
            backend = suspense_manager.tokens()
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
        place, _, name = unescape(found["transport"] or "").partition(":")
        token = unescape(found["token"])
        return self._reload(request.get_full_path(), found["id"], token, place, name)

    def _reload(
        self, url: str, ident: str, token: str, place: str, name: str
    ) -> PartialEnvelope:
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
            sent[HEADER] = token
            response = self.get(url, headers=sent)
        return envelope_of(cast("HttpResponse", response))

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

        Without `layouts` the origin loads as a document and its `$layouts` declare.
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
        with capture_signals(navigation_rendered) as recorder:
            response = self.get(path, headers=sent, **forwarded)
        return _navigation(response, recorder.events)

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
        names = zones if isinstance(zones, str) else ",".join(zones)
        sent = dict(headers or {})
        sent[REQUEST_FLAG] = "1"
        sent[ZONE] = names
        if version is not None:
            sent[VERSION] = version
        forwarded: dict[str, Any] = extra
        return cast("HttpResponse", self.get(url, headers=sent, **forwarded))


def _component(page_path: Path, name: str) -> ComponentInfo:
    """Return the component `name` a render of the page can call."""
    for info in page_components(page_path).values():
        if info.name == name:
            return info
    msg = f"no component named {name!r} is visible from {page_path}"
    raise LookupError(msg)


__all__ = [
    "Navigation",
    "NextClient",
    "PartialEnvelope",
    "declared_layouts",
    "envelope_of",
]
