"""The head tags and the manifest entries one script turns into."""

import re
from html import escape
from typing import Final

from next.static.runtime import nonce_attr

from .markers import SCRIPT_ATTR, Script, Strategy


_SCRIPT_MARKUP: Final = re.compile(r"<(/?script|!--)", re.IGNORECASE)
"""What the HTML parser reads inside a script element, see the HTML spec's advice."""
_LOAD_ATTRS: Final = {Strategy.ASYNC: " async", Strategy.DEFER: " defer"}


def inline_body(init: str) -> str:
    """Return an `init` body that cannot close or swallow the element it sits in.

    `</script` closes it, while `<!--` before `<script` keeps the real close from
    closing it, so each gains a backslash a string literal reads through.
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

    The runtime stamps the nonce the page booted with, the one its policy names.
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


__all__ = ["head_tags", "inline_body", "manifest_entry"]
