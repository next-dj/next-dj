"""The head tags and the manifest entries one script turns into."""

import hashlib
import re
from html import escape
from typing import Final

from next.static import StaticAsset
from next.static.runtime import nonce_attr

from .markers import SCRIPT_ATTR, Script, Strategy


_SCRIPT_MARKUP: Final = re.compile(r"<(/?script(?=[\s/>])|!--)", re.IGNORECASE)
"""The markup the HTML parser acts on inside a script element.

A tag name ends at whitespace, `/` or `>`, so `i<scripts.length` is left as written.
"""
_LOAD_ATTRS: Final = {Strategy.ASYNC: " async", Strategy.DEFER: " defer"}
_MODULE_KIND: Final = "module"
_INLINE_DIGEST: Final = 12


def inline_body(init: str) -> str:
    """Return an `init` body that cannot close or swallow the element it sits in.

    `</script` closes the element, and `<!--` before `<script` keeps the real end tag
    from closing it. Each gains a backslash, which a string literal ignores.
    """
    return _SCRIPT_MARKUP.sub(r"<\\\1", init)


def _attrs(pairs: dict[str, str]) -> str:
    return "".join(f' {name}="{escape(value)}"' for name, value in pairs.items())


def head_tags(script: Script, src: str | None, nonce: str | None) -> str:
    """Return the `init` and `src` tags of a script the server renders in the head.

    Both tags name the script, so the runtime never inserts it a second time.
    """
    named = f' {SCRIPT_ATTR}="{escape(script.name)}"'
    tags: list[str] = []
    if script.init is not None:
        body = inline_body(script.init)
        tags.append(f"<script{nonce_attr(nonce)}{named}>{body}</script>")
    if src is not None:
        load = _LOAD_ATTRS.get(script.strategy, "")
        extra = _attrs(script.allowed_attrs())
        tags.append(
            f'<script src="{escape(src)}"{load}{extra}{nonce_attr(nonce)}{named}>'
            "</script>"
        )
    return "\n".join(tags)


def manifest_entry(script: Script, src: str | None) -> dict[str, object]:
    """Return the `$scripts` entry the runtime loads a script from.

    The runtime adds the nonce the page loaded with, the one its policy names.
    """
    entry: dict[str, object] = {"name": script.name}
    if src is not None:
        entry["src"] = src
    if script.init is not None:
        entry["init"] = script.init
    entry["strategy"] = str(script.strategy)
    entry["category"] = script.category
    entry["attrs"] = script.allowed_attrs()
    return entry


def asset_entry(
    asset: StaticAsset, category: str, src: str | None
) -> dict[str, object]:
    """Return the `$scripts` entry of an asset a gated block held back.

    It loads in order with the other deferred entries, an inline body as its `init`.
    It is named by its URL, or by a digest of an inline body.
    """
    if asset.inline is not None:
        digest = hashlib.sha256(asset.inline.encode()).hexdigest()[:_INLINE_DIGEST]
        name = f"inline:{digest}"
    else:
        name = asset.url
    entry: dict[str, object] = {"name": name}
    if src is not None:
        entry["src"] = src
    if asset.inline is not None:
        entry["init"] = asset.inline
    entry["strategy"] = str(Strategy.DEFER)
    entry["category"] = category
    entry["attrs"] = {"type": "module"} if asset.kind == _MODULE_KIND else {}
    return entry


__all__ = ["asset_entry", "head_tags", "inline_body", "manifest_entry"]
