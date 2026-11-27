"""The wrapper markup of a deferred boundary and the streams that settle it.

A pure `Writer` frames each batch of settled pieces, `pump` and `apump` read the batches
and hand the bytes on, and a `Lifetime` owns the slot, the counts and the signals.
"""

from .chunks import Chunks, Payloads
from .document import Document, dress
from .lifetime import Batches, Lifetime, StreamGrant, release_connections
from .markup import idle, in_place, shell, strip, wrappers
from .page import StreamingPageResponse
from .partial import PartialStream
from .writer import Writer, apart, errored


__all__ = [
    "Batches",
    "Chunks",
    "Document",
    "Lifetime",
    "PartialStream",
    "Payloads",
    "StreamGrant",
    "StreamingPageResponse",
    "Writer",
    "apart",
    "dress",
    "errored",
    "idle",
    "in_place",
    "release_connections",
    "shell",
    "strip",
    "wrappers",
]
