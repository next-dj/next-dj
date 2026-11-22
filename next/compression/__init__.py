"""Response compression with zstd, brotli and gzip, negotiated per request."""

from . import checks
from .backends import (
    BrotliBackend,
    CompressionBackend,
    Compressor,
    GzipBackend,
    ZstdBackend,
)
from .decorators import no_compress
from .errors import CodecUnavailableError, CompressionBackendError
from .manager import (
    OPT_OUT,
    CompressionManager,
    acompress_response,
    compress_response,
    compression_manager,
    decode_response,
    offloads,
    prepare_stream,
)
from .negotiation import negotiate, parse_accept_encoding
from .signals import compression_backend_loaded
from .streams import StreamEncoding, padding


__all__ = [
    "OPT_OUT",
    "BrotliBackend",
    "CodecUnavailableError",
    "CompressionBackend",
    "CompressionBackendError",
    "CompressionManager",
    "Compressor",
    "GzipBackend",
    "StreamEncoding",
    "ZstdBackend",
    "acompress_response",
    "checks",
    "compress_response",
    "compression_backend_loaded",
    "compression_manager",
    "decode_response",
    "negotiate",
    "no_compress",
    "offloads",
    "padding",
    "parse_accept_encoding",
    "prepare_stream",
]
