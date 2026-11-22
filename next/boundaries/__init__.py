"""The inline error boundary, which contains a failed subtree behind its error UI."""

from .containment import contain, contain_bare
from .elements import misrooted, open_elements
from .failures import (
    Boundary,
    Fallback,
    Trace,
    contained,
    kind_of,
    new_digest,
    shown_kind,
)
from .markup import (
    ADOPTING_ELEMENTS,
    RETRY_ATTR,
    adopt,
    debug_detail,
    failed,
    identity,
    note_retry,
    wrapped,
    wrapper_tag,
)
from .options import errors_opaque
from .protection import aguarded, guarded, protect
from .scope import claim_wrapper, minting, render_boundaries, wrapping
from .signals import boundary_failed


__all__ = [
    "ADOPTING_ELEMENTS",
    "RETRY_ATTR",
    "Boundary",
    "Fallback",
    "Trace",
    "adopt",
    "aguarded",
    "boundary_failed",
    "claim_wrapper",
    "contain",
    "contain_bare",
    "contained",
    "debug_detail",
    "errors_opaque",
    "failed",
    "guarded",
    "identity",
    "kind_of",
    "minting",
    "misrooted",
    "new_digest",
    "note_retry",
    "open_elements",
    "protect",
    "render_boundaries",
    "shown_kind",
    "wrapped",
    "wrapper_tag",
    "wrapping",
]
