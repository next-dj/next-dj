"""System check for an inline script without a nonce in a page template."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from django.core.checks import CheckMessage, Warning as DjangoWarning

from next.checks import NEXT, register
from next.conf.router import router_enabled
from next.discovery import get_template_djx_paths_for_watch
from next.pages.loaders import template_source

from .layouts import layouts


if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


_INLINE_SCRIPT: Final = re.compile(r"<script\b(?P<attrs>[^>]*)>", re.IGNORECASE)
_SRC: Final = re.compile(r"\ssrc\s*=", re.IGNORECASE)
_NONCE: Final = re.compile(r"\snonce\s*=", re.IGNORECASE)
_TYPE: Final = re.compile(r"""\stype\s*=\s*["']?([^"'\s>]+)""", re.IGNORECASE)
_SCRIPT_TYPES: Final = frozenset(
    {"", "text/javascript", "application/javascript", "module"}
)


def _inline_scripts(text: str) -> int:
    """Return how many executable inline `<script>` tags of `text` carry no nonce."""
    count = 0
    for match in _INLINE_SCRIPT.finditer(text):
        attrs = match["attrs"]
        kind = _TYPE.search(attrs)
        if kind is not None and kind.group(1).lower() not in _SCRIPT_TYPES:
            continue
        if _SRC.search(attrs) is None and _NONCE.search(attrs) is None:
            count += 1
    return count


def _templates() -> Iterable[Path]:
    """Return every page and layout template of the page trees, sorted."""
    return sorted({*get_template_djx_paths_for_watch(), *layouts()})


@register(NEXT)
def check_inline_scripts(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about an inline script without a nonce in a template (`next.W177`)."""
    if not router_enabled():
        return []
    warnings: list[CheckMessage] = []
    for path in _templates():
        text = template_source(path)
        if text is not None and _inline_scripts(text):
            warnings.append(
                DjangoWarning(
                    f"{path} holds an inline <script> without a nonce, which does "
                    "not run after a client router navigation inserts it.",
                    hint="The router runs an inline script only when its nonce is "
                    'the response nonce, so add nonce="{{ csp_nonce }}" or '
                    'nonce="{{ request.csp_nonce }}" under a CSP middleware, or move '
                    "the code into a co-located script.",
                    obj=str(path),
                    id="next.W177",
                )
            )
    return warnings


__all__ = ["check_inline_scripts"]
