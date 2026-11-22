"""How a render delivers its deferred jobs, assembled inline or streamed after it."""

from .delivery import (
    InlineDeferrals,
    PageDeferrals,
    PageRender,
    assembled,
    inline_sink,
    page_deferring,
    page_sink,
)
from .inline import Run, assemble, finish, prune, run_sequentially
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
    "run_sequentially",
]
