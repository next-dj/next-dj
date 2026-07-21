from __future__ import annotations

from typing import TYPE_CHECKING
from unittest import mock

import pytest
from django.contrib.staticfiles.storage import staticfiles_storage
from django.template import Context, Template

from next.static import StaticCollector, StaticFilesBackend, StaticManager
from next.static.collector import HEAD_CLOSE


STYLES_PLACEHOLDER = "<!-- next:styles -->"
SCRIPTS_PLACEHOLDER = "<!-- next:scripts -->"


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


HTML_SHELL = (
    "<html><head>"
    f"{STYLES_PLACEHOLDER}"
    f"{HEAD_CLOSE}"
    f"<body>{SCRIPTS_PLACEHOLDER}</body></html>"
)


class _ManagerWithRoots(StaticManager):
    def __init__(self, roots: tuple[Path, ...]) -> None:
        super().__init__()
        self._cached_page_roots = roots


@pytest.fixture()
def wired_manager() -> StaticManager:
    manager = StaticManager()
    manager._backends = [StaticFilesBackend()]
    return manager


@pytest.fixture()
def static_url_mock() -> Iterator[None]:
    staticfiles_storage._setup()  # type: ignore[attr-defined]
    with mock.patch.object(
        staticfiles_storage._wrapped,  # type: ignore[attr-defined]
        "url",
        side_effect=lambda path: f"/static/{path}",
    ):
        yield


class TestFullRenderPipeline:
    """Discovery + inject glue produces a rendered HTML blob."""

    def test_template_css_and_module_scripts_flow_through(
        self, tmp_path: Path, collector: StaticCollector, static_url_mock: None
    ) -> None:
        (tmp_path / "template.css").write_text("body{color:red}")
        (tmp_path / "page.py").write_text(
            'scripts = ["https://cdn/x.js"]\nstyles = ["https://cdn/y.css"]\n'
        )
        page_path = tmp_path / "page.py"

        manager = _ManagerWithRoots((tmp_path.resolve(),))
        manager._backends = [StaticFilesBackend()]
        manager.discover_page_assets(page_path, collector)
        out = manager.inject(HTML_SHELL, collector, page_path=page_path)

        # Styles: template.css (via index.css) + module list styles
        assert "/static/next/index.css" in out
        assert "https://cdn/y.css" in out
        # Scripts: next.min.js first, then module scripts
        assert "next/next.min.js" in out
        assert "https://cdn/x.js" in out
        idx_next = out.index("next/next.min.js")
        idx_user = out.index("https://cdn/x.js")
        assert idx_next < idx_user
        # Preload hint
        assert 'rel="preload"' in out


class TestUseStyleBlocksThroughPipeline:
    """{% use_style %} registration order survives collector + inject."""

    def test_use_style_lands_in_final_html(
        self, wired_manager: StaticManager, collector: StaticCollector
    ) -> None:
        template = Template(
            "{% load next_static %}"
            '{% use_style "https://cdn/shared.css" %}'
            f"{STYLES_PLACEHOLDER}"
        )
        rendered = template.render(Context({"_static_collector": collector}))

        final = wired_manager.inject(rendered, collector)
        assert 'href="https://cdn/shared.css"' in final


class TestInlineBlocksThroughPipeline:
    """`{% #use_style %}` / `{% #use_script %}` reach the head as working tags."""

    def test_inline_blocks_land_wrapped(
        self, wired_manager: StaticManager, collector: StaticCollector
    ) -> None:
        template = Template(
            "{% load next_static %}"
            "{% #use_style %}.card{margin:0}{% /use_style %}"
            "{% #use_script %}window.x=1;{% /use_script %}"
            f"{STYLES_PLACEHOLDER}{SCRIPTS_PLACEHOLDER}"
        )
        rendered = template.render(Context({"_static_collector": collector}))

        final = wired_manager.inject(rendered, collector)
        assert "<style>.card{margin:0}</style>" in final
        assert "<script>window.x=1;</script>" in final


class TestJsContextFlowsThroughInit:
    def test_context_values_reach_init_script(
        self,
        wired_manager: StaticManager,
        collector: StaticCollector,
        static_url_mock: None,
    ) -> None:
        collector.add_js_context("user", "alice")
        collector.add_js_context("score", 42)

        out = wired_manager.inject(f"<body>{SCRIPTS_PLACEHOLDER}</body>", collector)
        assert 'Next._init({"user":"alice","score":42})' in out


class TestJsContextEscapedInInit:
    def test_script_delimiter_cannot_break_out_of_init(
        self,
        wired_manager: StaticManager,
        collector: StaticCollector,
        static_url_mock: None,
    ) -> None:
        collector.add_js_context("k", "</script><b>pwn")

        out = wired_manager.inject(f"<body>{SCRIPTS_PLACEHOLDER}</body>", collector)
        init_start = out.index("Next._init(")
        init_end = out.index("</script>", init_start)
        payload = out[init_start:init_end]
        assert "</script>" not in payload
        assert "\\u003C/script\\u003E\\u003Cb\\u003Epwn" in payload


class TestEmptyCollectorIntegration:
    def test_empty_collector_yields_next_runtime_only(
        self,
        wired_manager: StaticManager,
        collector: StaticCollector,
        static_url_mock: None,
    ) -> None:
        out = wired_manager.inject(
            f"<head>{HEAD_CLOSE}<body>{SCRIPTS_PLACEHOLDER}</body>", collector
        )
        assert "next/next.min.js" in out
        assert STYLES_PLACEHOLDER not in out
        assert SCRIPTS_PLACEHOLDER not in out
