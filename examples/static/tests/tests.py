from __future__ import annotations

import io
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.management import call_command
from django.test import override_settings
from myapp.apps import MyAppConfig
from myapp.custom_backend import AttributedStaticFilesBackend

from next.static import StaticFilesBackend, default_manager


if TYPE_CHECKING:
    from pathlib import Path

    from django.test import Client


@pytest.mark.parametrize(
    "url",
    [
        "/static/next/layout.css",
        "/static/next/index.css",
        "/static/next/components/widget.css",
        "/static/next/components/counter.css",
        "/static/next/layout.js",
        "/static/next/index.js",
        "/static/next/components/widget.js",
    ],
)
def test_home_contains_colocated_urls(home_html: str, url: str) -> None:
    assert url in home_html


@pytest.mark.parametrize(
    "url",
    [
        "/static/next/layout.css",
        "/static/next/dashboard.css",
        "/static/next/components/chart.css",
        "/static/next/layout.js",
        "/static/next/components/chart.js",
    ],
)
def test_dashboard_contains_colocated_urls(dashboard_html: str, url: str) -> None:
    assert url in dashboard_html


def test_example_uses_attributed_backend() -> None:
    assert isinstance(default_manager.default_backend, AttributedStaticFilesBackend)
    assert isinstance(default_manager.default_backend, StaticFilesBackend)


def test_app_config_metadata() -> None:
    assert MyAppConfig.name == "myapp"


def test_home_and_dashboard_routes_render(client: Client) -> None:
    assert client.get("/").status_code == 200
    assert client.get("/dashboard/").status_code == 200


def test_home_contains_next_min_js(home_html: str) -> None:
    assert "next/next.min.js" in home_html


def test_home_contains_next_init_with_page_meta(home_html: str) -> None:
    assert "Next._init(" in home_html
    assert '"page_meta"' in home_html
    assert '"home"' in home_html


def test_home_next_init_contains_theme(home_html: str) -> None:
    assert '"theme"' in home_html
    assert '"dark"' in home_html


def test_home_preload_hint_before_head_close(home_html: str) -> None:
    assert home_html.index('rel="preload"') < home_html.index("</head>")


def test_inline_page_renders_inline_css_and_js(client: Client) -> None:
    response = client.get("/inline/")
    assert response.status_code == 200
    body = response.content.decode()
    assert ".inline-highlight" in body
    assert "inline demo: page=" in body


def test_nested_docs_page_renders_three_layout_levels(client: Client) -> None:
    response = client.get("/docs/guide/intro/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "docs-wrapper" in body
    assert "docs-guide" in body
    assert "Nested layouts" in body
    assert "/static/next/docs/layout.css" in body
    assert "/static/next/docs/guide/layout.css" in body


def test_dashboard_exposes_unkeyed_context_and_depends(dashboard_html: str) -> None:
    assert "Active users" in dashboard_html
    assert ">42<" in dashboard_html
    assert ">128h<" in dashboard_html
    assert ">310<" in dashboard_html
    assert "<code>0.4</code>" in dashboard_html
    assert "demo" in dashboard_html
    assert "<code>dark</code>" in dashboard_html


def test_home_renders_next_plugin_component(home_html: str) -> None:
    assert "next-plugin-out" in home_html
    assert "Next.use" in home_html


def test_broken_page_emits_missing_asset_reference(client: Client) -> None:
    response = client.get("/broken/")
    assert response.status_code == 200
    assert "missing-asset.css" in response.content.decode()


def test_attributed_backend_injects_defer_and_crossorigin(home_html: str) -> None:
    assert 'defer crossorigin="anonymous"' in home_html
    assert 'crossorigin="anonymous"' in home_html


def test_attributed_backend_adds_integrity_attribute_when_mapped() -> None:
    backend = AttributedStaticFilesBackend(
        {
            "OPTIONS": {
                "integrity": {
                    "https://cdn.example.com/app.js": "sha384-abc",
                },
            },
        }
    )
    tag = backend.render_script_tag("https://cdn.example.com/app.js")
    assert 'integrity="sha384-abc"' in tag


def test_collectstatic_dry_run_lists_next_namespace(tmp_path: Path) -> None:
    static_root = tmp_path / "static_root"
    static_root.mkdir()
    out = io.StringIO()
    with override_settings(STATIC_ROOT=str(static_root)):
        call_command(
            "collectstatic",
            "--noinput",
            "--dry-run",
            "--ignore=*.py",
            stdout=out,
            verbosity=2,
        )
    output = out.getvalue()
    assert "Pretending to copy" in output
    assert "pages/layout.css" in output
    assert "pages/template.css" in output
    assert "static files copied" in output


def test_backend_raises_runtimeerror_when_manifest_missing(tmp_path: Path) -> None:
    staticfiles_storage._setup()
    backend = StaticFilesBackend({"OPTIONS": {}})
    with (
        mock.patch.object(
            staticfiles_storage._wrapped,
            "url",
            side_effect=ValueError("Missing staticfiles manifest entry"),
        ),
        pytest.raises(RuntimeError, match="missing from Django staticfiles"),
    ):
        backend.register_file(
            tmp_path / "layout.css",
            "layout",
            "css",
        )
