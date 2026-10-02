from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from django.test import Client

from tests.support import routed, write_page


if TYPE_CHECKING:
    from pathlib import Path

    from django.http import HttpResponse


HEAD = (
    "<html><head>{% collect_head %}<title>t</title></head>"
    "<body>{% template %}{% collect_scripts %}</body></html>"
)
NO_HEAD_TOKEN = (
    "<html><head><title>t</title></head>"
    "<body>{% template %}{% collect_scripts %}</body></html>"
)
SCRIPTS = """
from next.scripts import Script, Strategy

scripts = (
    Script("early", init="window.early=1", strategy=Strategy.BLOCKING),
    Script("base", src="https://cdn.example/base.js"),
    Script(
        "pixel",
        src="https://px.example/p.js",
        init="window.px=[]",
        category="marketing",
    ),
    Script("chat", src="https://chat.example/c.js", strategy=Strategy.IDLE),
    Script("optional", src="https://opt.example/o.js", auto=False),
    Script("ads", init="window.ads=1", category="marketing"),
)
"""
_INIT = re.compile(r"Next\._init\((\{.*?\})\);</script>", re.DOTALL)


def write_tree(
    root: Path,
    *,
    scripts: str | None = SCRIPTS,
    layout: str = HEAD,
    page: str = 'template = "<p>x</p>"\n',
    trail: str = "",
) -> Path:
    """Write a page tree with a layout and an optional `scripts.py`."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "layout.djx").write_text(layout)
    write_page(root, trail, page)
    if scripts is not None:
        (root / "scripts.py").write_text(scripts)
    return root


def get(
    root: Path,
    path: str = "/",
    *,
    cookie: str | None = None,
    extra_roots: tuple[Path, ...] = (),
    **framework: object,
) -> HttpResponse:
    """Render `path` of the tree at `root`, the consent cookie set when given."""
    client = Client()
    if cookie is not None:
        client.cookies["next_consent"] = cookie
    with routed(root, *extra_roots, **framework):
        return client.get(path)


def payload(response: HttpResponse) -> dict[str, object]:
    """Return the init payload the page hands to `Next._init`."""
    match = _INIT.search(response.content.decode())
    assert match is not None
    return json.loads(match.group(1))


def names(entries: object) -> list[str]:
    """Return the names of the `$scripts` entries in order."""
    assert isinstance(entries, list)
    return [entry["name"] for entry in entries]
