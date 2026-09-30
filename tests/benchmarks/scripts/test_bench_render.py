from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.scripts.manager import ScriptsManager
from next.scripts.registry import ScriptsRegistry
from next.static.collector import StaticCollector
from tests.support import consent_request, routed


if TYPE_CHECKING:
    from pathlib import Path


_SCRIPTS = """
from next.scripts import Script, Strategy

scripts = (
    Script("consent", init="window.dataLayer=[]", strategy=Strategy.BLOCKING),
    Script("base", src="https://cdn.example/base.js"),
    Script("gtm", src="https://gtm.example/gtm.js", category="analytics"),
    Script("pixel", src="https://px.example/p.js", init="px()", category="marketing"),
    Script("chat", src="https://chat.example/c.js", strategy=Strategy.IDLE),
    Script("maps", src="https://maps.example/m.js", auto=False),
)
"""


def _manager(root: Path, source: str | None) -> ScriptsManager:
    root.mkdir(parents=True, exist_ok=True)
    (root / "page.py").write_text('template = "x"\n')
    if source is not None:
        (root / "scripts.py").write_text(source)
    manager = ScriptsManager(ScriptsRegistry())
    with routed(root):
        manager.sources()
    return manager


class TestBenchScriptsRender:
    """The plan one render follows, the part every page with scripts pays for."""

    @pytest.mark.benchmark(group="scripts.render")
    def test_render_without_scripts(self, benchmark, tmp_path: Path) -> None:
        """A tree without `scripts.py`, the cost every other page pays."""
        root = tmp_path / "pages"
        manager = _manager(root, None)
        page = root / "page.py"
        benchmark(
            lambda: manager.render(
                StaticCollector(), page_path=page, request=None, nonce=None
            )
        )

    @pytest.mark.benchmark(group="scripts.render")
    def test_render_undecided(self, benchmark, tmp_path: Path) -> None:
        """A first visit, every gated script sent to the manifest."""
        root = tmp_path / "pages"
        manager = _manager(root, _SCRIPTS)
        page = root / "page.py"
        benchmark(
            lambda: manager.render(
                StaticCollector(),
                page_path=page,
                request=consent_request(None),
                nonce="n0",
            )
        )

    @pytest.mark.benchmark(group="scripts.render")
    def test_render_granted(self, benchmark, tmp_path: Path) -> None:
        """A returning visitor, the granted scripts rendered in the head."""
        root = tmp_path / "pages"
        manager = _manager(root, _SCRIPTS)
        page = root / "page.py"
        benchmark(
            lambda: manager.render(
                StaticCollector(),
                page_path=page,
                request=consent_request("1:analytics,marketing:1"),
                nonce="n0",
            )
        )
