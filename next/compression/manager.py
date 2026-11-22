"""The compression façade, backends built on first use, eligibility and negotiation."""

import asyncio
import functools
import logging
import re
import threading
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final, cast

import django
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.http.response import HttpResponseBase
from django.utils.cache import patch_vary_headers

from next.conf.compression import (
    OPT_OUT,
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
from .streams import EVENT_STREAM, StreamEncoding, media_type, padding


MIDDLEWARE: Final = "next.middleware.CompressionMiddleware"
GZIP_MIDDLEWARE: Final = "django.middleware.gzip.GZipMiddleware"

logger = logging.getLogger(__name__)

_SKIPPED_STATUSES: Final = frozenset({204, 206, 304})
_FIRST_FINAL_STATUS: Final = 200
_NO_TRANSFORM: Final = "no-transform"
_FILE: Final = "file_to_stream"
_IDENTITY: Final = "identity"
_ACCEPT: Final = "HTTP_ACCEPT_ENCODING"
_HEAD: Final = "HEAD"
_WILDCARD: Final = "/*"
_HTML: Final = "text/html"
_GZIP: Final = "gzip"
_ACCEPTS_GZIP: Final = re.compile(r"\bgzip\b")
_FLUSHING_GZIP: Final = (6, 1)

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


def _load_backend(dotted: str) -> CompressionBackend | None:
    """Build the backend `dotted` names, `None` for a path that names no backend."""
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
        sender=klass, config={"BACKEND": dotted}, instance=backend
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


def in_range(value: object, bounds: tuple[int, int] | None) -> bool:
    """Whether `value` is an integer within the inclusive `bounds`."""
    return (
        isinstance(value, int)
        and bounds is not None
        and bounds[0] <= value <= bounds[1]
    )


def _level(backend: CompressionBackend, levels: Mapping[str, int]) -> int:
    """Return the `LEVELS` entry of the codec, its own default when out of range."""
    level = levels.get(backend.coding)
    if level is not None and in_range(level, backend.levels):
        return level
    return backend.level


def _window(backend: CompressionBackend, windows: Mapping[str, int]) -> int | None:
    """Return the `WINDOWS` entry of the codec, its own default when out of range."""
    window = windows.get(backend.coding)
    if window is not None and in_range(window, backend.windows):
        return window
    return backend.window


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
        """Return a stream compressor of `backend` at its level and window."""
        coding = backend.coding
        return backend.create(self.levels[coding], self.windows[coding])


def _build(settings: CompressionSettings) -> _State:
    """Load the `BACKENDS` and pick the available codings `ORDER` offers."""
    loaded: dict[str, CompressionBackend] = {}
    for dotted in settings.backends:
        backend = _load_backend(dotted)
        if backend is not None:
            loaded.setdefault(backend.coding, backend)
    chosen = (loaded.get(coding) for coding in dict.fromkeys(settings.order))
    usable = [b for b in chosen if b is not None and b.available()]
    return _State(
        settings=settings,
        loaded=loaded,
        offered=tuple(b.coding for b in usable),
        resetting=tuple(b.coding for b in usable if b.resets),
        levels={b.coding: _level(b, settings.levels) for b in loaded.values()},
        windows={b.coding: _window(b, settings.windows) for b in loaded.values()},
        exclude=_load_exclude(settings.exclude),
    )


@functools.lru_cache(maxsize=8)
def _media_sets(types: tuple[str, ...]) -> tuple[frozenset[str], frozenset[str]]:
    """Split `CONTENT_TYPES` into exact media types and `type/*` families."""
    cut = len(_WILDCARD)
    families = frozenset(item[:-cut] for item in types if item.endswith(_WILDCARD))
    exact = frozenset(item for item in types if not item.endswith(_WILDCARD))
    return exact, families


def compressible(media: str, settings: CompressionSettings) -> bool:
    """Whether `CONTENT_TYPES` lists the media type, exactly or by its family."""
    exact, families = _media_sets(settings.content_types)
    return media in exact or media.partition("/")[0] in families


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
        if request.method == _HEAD:
            return None
        offered = state.resetting if resetting else state.offered
        coding = negotiate(request.META.get(_ACCEPT, ""), offered)
        if coding is None:
            return None
        return state, state.loaded[coding]

    def document_coding(self, request: HttpRequest) -> tuple[str | None, bool]:
        """Return the coding a document stream reaches `request` in, and if it buffers.

        Django's `GZipMiddleware` buffers a stream below 6.1 unless it encodes itself.
        """
        settings = compression_settings()
        accept = request.META.get(_ACCEPT, "")
        if settings.enabled and compressible(_HTML, settings):
            coding = negotiate(accept, self._ensure_backends().offered)
            if coding is not None and (
                settings.streams == StreamOwner.SELF or middleware_installed(MIDDLEWARE)
            ):
                return coding, False
        if middleware_installed(GZIP_MIDDLEWARE) and _ACCEPTS_GZIP.search(accept):
            return _GZIP, django.VERSION[:2] < _FLUSHING_GZIP
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


def _stream_through(
    response: StreamingHttpResponse, state: _State, backend: CompressionBackend
) -> None:
    """Replace the stream of `response` with its encoding, flushed per chunk."""
    encoding = StreamEncoding(
        backend.coding,
        state.create(backend),
        offload_bytes=state.settings.offload_bytes,
    )
    if response.is_async:
        source = cast("AsyncIterator[bytes]", response.streaming_content)
        response.streaming_content = encoding.awrap(source)
    else:
        chunks = cast("Iterator[bytes]", response.streaming_content)
        response.streaming_content = encoding.wrap(chunks)
    del response.headers["Content-Length"]
    _mark_encoded(response, backend.coding)


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

    def compress(self) -> bytes:
        """Return the body compressed in one shot."""
        return self.backend.compress_once(self.body, self.level)

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
            _stream_through(response, *streamed)
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
    body = content + padding(response.get("Content-Type", ""), state.settings.padding)
    return _Buffered(response, backend, state.levels[backend.coding], body, size)


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
    return len(data) > compression_settings().offload_bytes


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
    del response.headers["Content-Length"]
    _mark_encoded(response, backend.coding)
    return StreamEncoding(
        backend.coding,
        state.create(backend),
        content_type=content_type,
        pad=settings.padding,
        offload_bytes=settings.offload_bytes,
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


__all__ = [
    "GZIP_MIDDLEWARE",
    "MIDDLEWARE",
    "OPT_OUT",
    "CompressionManager",
    "acompress_response",
    "compress_response",
    "compressible",
    "compression_manager",
    "decode_response",
    "forget_compression",
    "in_range",
    "offloads",
    "prepare_stream",
    "response_decoder",
]
