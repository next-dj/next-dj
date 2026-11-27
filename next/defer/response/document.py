"""The shell of a streamed page, cut where its chunks go and marked as streamed."""

import re
from dataclasses import dataclass
from typing import Final

from django.utils.html import escape

from next.partial.keys import STATE_ATTR, STREAMED_ATTR


_ROOT: Final = re.compile(r"<(?:html|body)(?=[\s>])", re.IGNORECASE)
_HEAD_OPEN: Final = re.compile(r"<head(?:\s[^>]*)?>", re.IGNORECASE)
_HEAD_CLOSE: Final = "</head>"
_REFRESH: Final = '<meta http-equiv="refresh" content="0;url={url}">'
_HIDDEN: Final = f"<style{{nonce}}>[{STATE_ATTR}=loading]{{{{display:none}}}}</style>"
_STREAM_ATTR: Final = f" {STREAMED_ATTR}"


@dataclass(frozen=True, slots=True)
class Document:
    """The shell of a streamed page cut where its chunks go."""

    head: str
    tail: str


def _nonce(nonce: str | None) -> str:
    return "" if nonce is None else f' nonce="{escape(nonce)}"'


def _noscript(nonce: str | None, refresh: str | None) -> str:
    """Return the `<noscript>` leading to `refresh` and hiding each loading wrapper."""
    lead = "" if refresh is None else _REFRESH.format(url=escape(refresh))
    return f"<noscript>{lead}{_HIDDEN.format(nonce=_nonce(nonce))}</noscript>"


def dress(
    html: str,
    split: int,
    *,
    nonce: str | None,
    noscript: bool,
    refresh: str | None = None,
) -> Document:
    """Return the shell cut at `split`, marked as streamed and led on without scripts.

    A reader without scripts goes on to `refresh`, the page assembled whole.
    """
    head, tail = html[:split], html[split:]
    root = _ROOT.search(head)
    if root is not None:
        at = root.end()
        head = f"{head[:at]}{_STREAM_ATTR}{head[at:]}"
    if noscript:
        block = _noscript(nonce, refresh)
        opened = _HEAD_OPEN.search(head)
        at = head.find(_HEAD_CLOSE) if opened is None else opened.end()
        head = head + block if at < 0 else f"{head[:at]}{block}{head[at:]}"
    return Document(head, tail)


__all__ = ["Document", "dress"]
