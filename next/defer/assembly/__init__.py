"""How a render delivers its deferred jobs, assembled inline or streamed after it.

The sink asks the policy once per request, then splices or shelves for a stream.
"""

from .delivery import (
    InlineDeferrals,
    PageDeferrals,
    PageRender,
    assembled,
    inline_sink,
    page_deferring,
    page_sink,
)
from .inline import Run, assemble, finish, prune
from .pages import page_response
from .partials import PartialRender


__all__ = [
    "InlineDeferrals",
    "PageDeferrals",
    "PageRender",
    "PartialRender",
    "Run",
    "assemble",
    "assembled",
    "finish",
    "inline_sink",
    "page_deferring",
    "page_response",
    "page_sink",
    "prune",
]
