"""The compression façade, backends built on first use, eligibility and negotiation."""

import asyncio
import logging
import re
import threading
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final, TypeGuard, cast

import django
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.http.response import HttpResponseBase
from django.utils.cache import patch_vary_headers

from next.caches import FRAMEWORK, pure_cache
from next.conf.compression import (
    OPT_OUT,
    CodecEntry,
    CompressionSettings,
    StreamOwner,
    TopicMode,
    compression_settings,
)
from next.conf.imports import (
    class_fault,
    import_backend_class,
    import_problem,
    import_target,
)
from next.conf.settings import fail_loudly
from next.utils import middleware_installed

from .backends import CompressionBackend, Compressor, Decoder
from .errors import CompressionBackendError
from .negotiation import negotiate
from .signals import compression_backend_loaded
from .streams import EVENT_STREAM, OFFLOAD_BYTES, StreamEncoding, media_type, padding


MIDDLEWARE: Final = "next.middleware.CompressionMiddleware"
GZIP_MIDDLEWARE: Final = "django.middleware.gzip.GZipMiddleware"
FLUSHING_GZIP: Final = (6, 1)
"""The Django version whose `GZipMiddleware` flushes each chunk of a stream."""

logger = logging.getLogger(__name__)

_SKIPPED_STATUSES: Final = frozenset({204, 206, 304})
_FIRST_FINAL_STATUS: Final = 200
_NO_TRANSFORM: Final = "no-transform"
_FILE: Final = "file_to_stream"
_IDENTITY: Final = "identity"
_ACCEPT: Final = "HTTP_ACCEPT_ENCODING"
_HEAD: Final = "HEAD"
_ANY: Final = "*"
_ANY_SUFFIXED: Final = "*+"
_HTML: Final = "text/html"
_GZIP: Final = "gzip"
_ACCEPTS_GZIP: Final = re.compile(r"\bgzip\b")

type Exclude = Callable[[HttpRequest, HttpResponseBase], object]


def _refused(
    key: str, dotted: str, problem: str, cause: ImportError | None = None
) -> None:
    """Raise the path error under `DEBUG` or `STRICT_LOADING`, else log and skip it.

    The log carries the import failure behind it, so the module's traceback survives.
    """
    error = CompressionBackendError(key, dotted, problem)
    if fail_loudly():
        raise error from cause
    logger.warning("%s The framework skips it.", error, exc_info=cause)


def _load_backend(entry: CodecEntry) -> CompressionBackend | None:
    """Build the backend `entry` names, `None` for a path that names no backend."""
    dotted = entry.path
    try:
        klass: type[CompressionBackend] = import_backend_class(dotted)
    except ImportError as exc:
        _refused("BACKENDS", dotted, import_problem(exc), exc)
        return None
    problem = class_fault(klass, CompressionBackend)
    if problem is not None:
        _refused("BACKENDS", dotted, problem)
        return None
    backend = klass()
    compression_backend_loaded.send(
        sender=klass, config=dict(entry.config), instance=backend
    )
    return backend


def _load_exclude(dotted: str | None) -> Exclude | None:
    """Import the `EXCLUDE` callable, `None` without one or for an unusable path."""
    if dotted is None:
        return None
    try:
        target = import_target(dotted)
    except ImportError as exc:
        _refused("EXCLUDE", dotted, import_problem(exc), exc)
        return None
    if not callable(target):
        _refused("EXCLUDE", dotted, "names no callable")
        return None
    return target


def in_range(value: object, bounds: tuple[int, int] | None) -> TypeGuard[int]:
    """Whether `value` is an integer within the inclusive `bounds`."""
    return (
        isinstance(value, int)
        and bounds is not None
        and bounds[0] <= value <= bounds[1]
    )


def _level(backend: CompressionBackend, entry: CodecEntry) -> int:
    """Return the `LEVEL` option of the entry, the codec default when out of range."""
    level = entry.level
    return level if in_range(level, backend.levels) else backend.level


def _window(backend: CompressionBackend, entry: CodecEntry) -> int | None:
    """Return the `WINDOW` option of the entry, the codec default when out of range."""
    window = entry.window
    return window if in_range(window, backend.windows) else backend.window


@dataclass(frozen=True, slots=True)
class _State:
    """The backends one `COMPRESSION` mapping builds, with the codings it offers."""

    settings: CompressionSettings
    loaded: Mapping[str, CompressionBackend]
    offered: tuple[str, ...]
    resetting: tuple[str, ...]
    levels: Mapping[str, int]
    windows: Mapping[str, int | None]
    exclude: Exclude | None

    def create(self, backend: CompressionBackend) -> Compressor:
        """Return a stream compressor of `backend` at its level, window and padding."""
        coding = backend.coding
        return backend.create(
            self.levels[coding], self.windows[coding], pad=self.settings.padding
        )


def _build(settings: CompressionSettings) -> _State:
    """Load the `BACKENDS` and pick the available codings `ORDER` offers."""
    loaded: dict[str, CompressionBackend] = {}
    levels: dict[str, int] = {}
    windows: dict[str, int | None] = {}
    for entry in settings.backends:
        backend = _load_backend(entry)
        if backend is not None and backend.coding not in loaded:
            loaded[backend.coding] = backend
            levels[backend.coding] = _level(backend, entry)
            windows[backend.coding] = _window(backend, entry)
    chosen = (loaded.get(coding) for coding in dict.fromkeys(settings.order))
    usable = [b for b in chosen if b is not None and b.available()]
    return _State(
        settings=settings,
        loaded=loaded,
        offered=tuple(b.coding for b in usable),
        resetting=tuple(b.coding for b in usable if b.resets),
        levels=levels,
        windows=windows,
        exclude=_load_exclude(settings.exclude),
    )


@dataclass(frozen=True, slots=True)
class _MediaSets:
    """`CONTENT_TYPES` split into exact types, `type/*` families and `type/*+suffix`."""

    exact: frozenset[str]
    families: frozenset[str]
    suffixed: frozenset[tuple[str, str]]


@pure_cache(8)
def _media_sets(types: tuple[str, ...]) -> _MediaSets:
    """Split `CONTENT_TYPES` by the way each entry matches a media type."""
    exact: set[str] = set()
    families: set[str] = set()
    suffixed: set[tuple[str, str]] = set()
    for item in types:
        family, _, subtype = item.partition("/")
        if subtype == _ANY:
            families.add(family)
        elif subtype.startswith(_ANY_SUFFIXED):
            suffixed.add((family, subtype[len(_ANY) :]))
        else:
            exact.add(item)
    return _MediaSets(frozenset(exact), frozenset(families), frozenset(suffixed))


def compressible(media: str, settings: CompressionSettings) -> bool:
    """Whether `CONTENT_TYPES` lists the media type, exactly, by family or by suffix.

    `application/*+json` matches every subtype with the structured suffix `+json`.
    """
    sets = _media_sets(settings.content_types)
    if media in sets.exact:
        return True
    family, _, subtype = media.partition("/")
    if family in sets.families:
        return True
    plus = subtype.rfind("+")
    return plus > 0 and (family, subtype[plus:]) in sets.suffixed


def _no_transform(response: HttpResponseBase) -> bool:
    """Whether the `Cache-Control` of `response` carries `no-transform`."""
    return any(
        part.strip().lower() == _NO_TRANSFORM
        for part in response.get("Cache-Control", "").split(",")
    )


def _encodable(response: HttpResponseBase, settings: CompressionSettings) -> bool:
    """Whether nothing about `response` itself forbids a content coding.

    A file response keeps the server's file path, its blocks too small to flush each.
    """
    status = response.status_code
    if (
        not settings.enabled
        or response.has_header("Content-Encoding")
        or status < _FIRST_FINAL_STATUS
        or status in _SKIPPED_STATUSES
        or response.has_header("Content-Range")
        or getattr(response, OPT_OUT, True) is False
        or getattr(response, _FILE, None) is not None
    ):
        return False
    return not (settings.respect_no_transform and _no_transform(response))


def eligible(
    response: HttpResponseBase, settings: CompressionSettings, *, events: bool
) -> bool:
    """Whether the response and its media type allow a coding, its body left unread."""
    if not _encodable(response, settings):
        return False
    media = media_type(response.get("Content-Type", ""))
    return (media != EVENT_STREAM or events) and compressible(media, settings)


def encodes_documents(settings: CompressionSettings) -> bool:
    """Whether next.dj encodes a document stream, itself or through its middleware."""
    return (
        settings.enabled
        and compressible(_HTML, settings)
        and (settings.streams == StreamOwner.SELF or middleware_installed(MIDDLEWARE))
    )


class CompressionManager:
    """Hold the codec backends of the process, built on the first compressible response.

    The backends follow the settings, built again once the `COMPRESSION` mapping moves.
    """

    def __init__(self) -> None:
        """Hold no backend until the first use."""
        self._lock = threading.Lock()
        self._state: _State | None = None

    @property
    def settings(self) -> CompressionSettings:
        """Return the `COMPRESSION` settings without building any backend."""
        return compression_settings()

    def _ensure_backends(self) -> _State:
        """Build the backends once per settings, double-checked under the lock."""
        settings = compression_settings()
        state = self._state
        if state is None or state.settings is not settings:
            with self._lock:
                state = self._state
                if state is None or state.settings is not settings:
                    state = _build(settings)
                    self._state = state
        return state

    def backend(self, coding: str) -> CompressionBackend | None:
        """Return the loaded backend of `coding`, the first `BACKENDS` names for it."""
        return self._ensure_backends().loaded.get(coding)

    def offered(self) -> tuple[str, ...]:
        """Return the available codings in server order, the ones negotiation picks."""
        return self._ensure_backends().offered

    def negotiated(
        self,
        request: HttpRequest,
        response: HttpResponseBase,
        *,
        events: bool,
        resetting: bool = False,
    ) -> tuple[_State, CompressionBackend] | None:
        """Return the backend to encode `response` with, patching `Vary` once it may.

        `resetting` keeps the codecs that drop their window between messages.
        """
        if not eligible(response, compression_settings(), events=events):
            return None
        return self.chosen(request, response, resetting=resetting)

    def chosen(
        self,
        request: HttpRequest,
        response: HttpResponseBase,
        *,
        resetting: bool = False,
    ) -> tuple[_State, CompressionBackend] | None:
        """Return the backend for an eligible `response`, patching `Vary` as it may."""
        state = self._ensure_backends()
        if state.exclude is not None and state.exclude(request, response):
            return None
        patch_vary_headers(response, ("Accept-Encoding",))
        offered = state.resetting if resetting else state.offered
        coding = negotiate(request.META.get(_ACCEPT, ""), offered)
        if coding is None:
            return None
        return state, state.loaded[coding]

    def document_coding(self, request: HttpRequest) -> tuple[str | None, bool]:
        """Return the coding a document stream reaches `request` in, and if it buffers.

        Django's `GZipMiddleware` buffers a stream below 6.1 unless it encodes itself.
        """
        accept = request.META.get(_ACCEPT, "")
        if encodes_documents(compression_settings()):
            coding = negotiate(accept, self._ensure_backends().offered)
            if coding is not None:
                return coding, False
        if middleware_installed(GZIP_MIDDLEWARE) and _ACCEPTS_GZIP.search(accept):
            return _GZIP, django.VERSION[:2] < FLUSHING_GZIP
        return None, False

    def reset(self) -> None:
        """Drop the backends, so the next use reads the settings again."""
        with self._lock:
            self._state = None


compression_manager = CompressionManager()


def forget_compression(**kwargs: object) -> None:
    """Reset the manager when the framework settings reload."""
    compression_manager.reset()


def _mark_encoded(response: HttpResponseBase, coding: str) -> None:
    """Set `Content-Encoding` and weaken a strong `ETag`, as the body changed bytes."""
    etag = response.get("ETag")
    if etag and etag.startswith('"'):
        response.headers["ETag"] = "W/" + etag
    response.headers["Content-Encoding"] = coding


def _mark_unsized(response: HttpResponseBase, coding: str) -> None:
    """Mark `response` encoded in `coding` and drop the length of the plain body."""
    del response.headers["Content-Length"]
    _mark_encoded(response, coding)


def _stream_through(
    request: HttpRequest,
    response: StreamingHttpResponse,
    state: _State,
    backend: CompressionBackend,
) -> None:
    """Replace the stream of `response` with its encoding, each sync chunk flushed.

    An async stream coalesces writes within `COALESCE`, and HEAD keeps its body as is.
    """
    _mark_unsized(response, backend.coding)
    if request.method == _HEAD:
        return
    encoding = StreamEncoding(backend.coding, state.create(backend))
    if response.is_async:
        events = media_type(response.get("Content-Type", "")) == EVENT_STREAM
        source = cast("AsyncIterator[bytes]", response.streaming_content)
        response.streaming_content = (
            encoding.awrap(source)
            if events
            else encoding.acoalesce(source, state.settings.coalesce)
        )
    else:
        chunks = cast("Iterator[bytes]", response.streaming_content)
        response.streaming_content = encoding.wrap(chunks)


@dataclass(frozen=True, slots=True)
class _Buffered:
    """One buffered body to compress once, padded, with its backend and level.

    `size` is the length of the body the response holds, read before the padding.
    """

    response: HttpResponse
    backend: CompressionBackend
    level: int
    body: bytes
    size: int
    pad: int

    def compress(self) -> bytes:
        """Return the body compressed in one shot."""
        return self.backend.compress_once(self.body, self.level, pad=self.pad)

    def apply(self, compressed: bytes) -> HttpResponse:
        """Keep the compressed body only when it is smaller than the original."""
        response = self.response
        if len(compressed) < self.size:
            response.content = compressed
            response.headers["Content-Length"] = str(len(compressed))
            _mark_encoded(response, self.backend.coding)
        return response


def _plan(
    request: HttpRequest, response: HttpResponseBase
) -> HttpResponseBase | _Buffered:
    """Encode a stream in place and return a buffered body still to compress.

    The response comes back as is when nothing is left to compress.
    """
    settings = compression_settings()
    if isinstance(response, StreamingHttpResponse):
        streamed = compression_manager.negotiated(
            request, response, events=settings.sse
        )
        if streamed is not None:
            _stream_through(request, response, *streamed)
        return response
    if not isinstance(response, HttpResponse) or not eligible(
        response, settings, events=settings.sse
    ):
        return response
    content = response.content
    size = len(content)
    if size < settings.min_size:
        return response
    found = compression_manager.chosen(request, response)
    if found is None:
        return response
    state, backend = found
    if request.method == _HEAD:
        _mark_unsized(response, backend.coding)
        return response
    pad = state.settings.padding
    body = content + padding(response.get("Content-Type", ""), pad)
    return _Buffered(response, backend, state.levels[backend.coding], body, size, pad)


def compress_response(
    request: HttpRequest, response: HttpResponseBase
) -> HttpResponseBase:
    """Return `response` encoded in the coding negotiated with the client, if any."""
    plan = _plan(request, response)
    if isinstance(plan, _Buffered):
        return plan.apply(plan.compress())
    return plan


def offloads(data: bytes) -> bool:
    """Whether `data` is above `OFFLOAD_BYTES`, so async code compresses it away."""
    return len(data) > OFFLOAD_BYTES


async def acompress_response(
    request: HttpRequest, response: HttpResponseBase
) -> HttpResponseBase:
    """Return `compress_response`, a body past `OFFLOAD_BYTES` compressed off-loop."""
    plan = _plan(request, response)
    if not isinstance(plan, _Buffered):
        return plan
    if offloads(plan.body):
        return plan.apply(await asyncio.to_thread(plan.compress))
    return plan.apply(plan.compress())


def prepare_stream(
    request: HttpRequest, response: HttpResponseBase, *, topic: bool = False
) -> StreamEncoding | None:
    """Negotiate the coding of a stream that compresses itself and set its headers.

    `None` sends the stream plain, or leaves a document stream to the middleware.
    """
    settings = compression_settings()
    content_type = response.get("Content-Type", "")
    events = media_type(content_type) == EVENT_STREAM
    if not events and settings.streams != StreamOwner.SELF:
        return None
    mode = settings.sse_topics if topic else None
    found: tuple[_State, CompressionBackend] | None = None
    if mode != TopicMode.OFF:
        found = compression_manager.negotiated(
            request,
            response,
            events=topic or settings.sse,
            resetting=mode == TopicMode.RESET,
        )
    if found is None:
        setattr(response, OPT_OUT, False)
        return None
    state, backend = found
    _mark_unsized(response, backend.coding)
    return StreamEncoding(
        backend.coding,
        state.create(backend),
        content_type=content_type,
        pad=settings.padding,
        reset=mode == TopicMode.RESET,
    )


def _identity(data: bytes) -> bytes:
    return data


def response_decoder(response: HttpResponseBase) -> Decoder:
    """Return the write-by-write decoder of the coding `response` declares.

    A response without `Content-Encoding` decodes as itself.
    """
    coding = response.get("Content-Encoding", "").strip().lower()
    if not coding or coding == _IDENTITY:
        return _identity
    backend = compression_manager.backend(coding)
    if backend is None:
        msg = f"No COMPRESSION backend decodes the coding {coding!r}."
        raise ValueError(msg)
    return backend.decoder()


def decode_response(response: HttpResponseBase) -> bytes:
    """Return the whole body of `response` decoded from its `Content-Encoding`."""
    decode = response_decoder(response)
    return b"".join(decode(part) for part in cast("Iterable[bytes]", response))


FRAMEWORK.attach(forget_compression)


__all__ = [
    "FLUSHING_GZIP",
    "GZIP_MIDDLEWARE",
    "MIDDLEWARE",
    "OPT_OUT",
    "CompressionManager",
    "acompress_response",
    "compress_response",
    "compressible",
    "compression_manager",
    "decode_response",
    "encodes_documents",
    "forget_compression",
    "in_range",
    "offloads",
    "prepare_stream",
    "response_decoder",
]
