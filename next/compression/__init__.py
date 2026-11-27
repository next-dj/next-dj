"""Response compression with zstd, brotli and gzip, negotiated per request."""

from .backends import (
    BrotliBackend,
    CompressionBackend,
    Compressor,
    GzipBackend,
    ZstdBackend,
)
from .decorators import no_compress
from .errors import CodecUnavailableError, CompressionBackendError
from .manager import decode_response
from .signals import compression_backend_loaded


__all__ = [
    "BrotliBackend",
    "CodecUnavailableError",
    "CompressionBackend",
    "CompressionBackendError",
    "Compressor",
    "GzipBackend",
    "ZstdBackend",
    "compression_backend_loaded",
    "decode_response",
    "no_compress",
]
