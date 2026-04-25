from __future__ import annotations

import io
from pathlib import Path

from django.core.cache import cache
from django.core.management import call_command
from shortener.cache import CLICK_PREFIX, increment_clicks, pending_clicks
from shortener.models import Link

from next.testing import assert_has_class, assert_missing_class, find_anchor


class TestShorten:
    """Submitting the form creates a Link and redirects home."""

    def test_post_creates_link_and_redirects(self, client) -> None:
        response = client.post_action("create_link", {"url": "https://example.com/a"})
        assert response.status_code == 302
        assert response["Location"] == "/"
        assert Link.objects.count() == 1

    def test_invalid_url_renders_form_with_errors(self, client) -> None:
        page_path = (
            Path(__file__).resolve().parent.parent / "shortener" / "routes" / "page.py"
        )
        response = client.post_action(
            "create_link",
            {"url": "not-a-url", "_next_form_page": str(page_path)},
        )
        assert response.status_code == 200
        assert b"Enter a valid URL" in response.content
        assert Link.objects.count() == 0


class TestSlugRedirect:
    """Visiting /s/<slug>/ redirects to the original URL and bumps the counter."""

    def test_slug_redirects_and_counts_click(self, client) -> None:
        Link.objects.create(slug="abc123", url="https://example.com/real")
        response = client.get("/s/abc123/")
        assert response.status_code == 302
        assert response["Location"] == "https://example.com/real"
        assert cache.get(f"{CLICK_PREFIX}abc123") == 1

    def test_missing_slug_returns_404(self, client) -> None:
        response = client.get("/s/missing/")
        assert response.status_code == 404


class TestAdminLinkDetail:
    """The admin link detail page uses DLink to resolve by slug."""

    def test_detail_renders_link_via_dlink_provider(self, client) -> None:
        Link.objects.create(slug="detailed", url="https://example.com/d", clicks=12)
        response = client.get("/admin/links/detailed/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "detailed" in body
        assert "https://example.com/d" in body
        assert f"{CLICK_PREFIX}detailed" in body
        assert "12" in body

    def test_detail_unknown_slug_returns_404(self, client) -> None:
        response = client.get("/admin/links/ghost/")
        assert response.status_code == 404


class TestFlushClicks:
    """The management command persists cached counters."""

    def test_flush_transfers_pending_clicks_to_db(self) -> None:
        Link.objects.create(slug="abc123", url="https://example.com/a")
        Link.objects.create(slug="def456", url="https://example.com/b")
        increment_clicks("abc123")
        increment_clicks("abc123")
        increment_clicks("def456")

        buf = io.StringIO()
        call_command("flush_clicks", stdout=buf)

        assert "flushed 3 clicks" in buf.getvalue()
        assert Link.objects.get(slug="abc123").clicks == 2
        assert Link.objects.get(slug="def456").clicks == 1
        assert pending_clicks() == {}


class TestActiveNav:
    """Active link highlighting uses `request.resolver_match.view_name`."""

    def test_admin_subnav_highlights_links_when_on_admin_index(self, client) -> None:
        body = client.get("/admin/").content.decode()
        assert_has_class(
            find_anchor(body, href="/admin/", text="Links"), "font-semibold"
        )
        assert_missing_class(
            find_anchor(body, href="/admin/stats/", text="Stats"),
            "font-semibold",
        )

    def test_admin_subnav_highlights_stats_when_on_stats(self, client) -> None:
        body = client.get("/admin/stats/").content.decode()
        assert_missing_class(
            find_anchor(body, href="/admin/", text="Links"),
            "font-semibold",
        )
        assert_has_class(
            find_anchor(body, href="/admin/stats/", text="Stats"),
            "font-semibold",
        )

    def test_root_admin_link_is_active_on_detail_page(self, client) -> None:
        Link.objects.create(slug="deep", url="https://example.com/d")
        body = client.get("/admin/links/deep/").content.decode()
        assert_has_class(
            find_anchor(body, href="/admin/", text="admin"), "font-semibold"
        )

    def test_root_admin_link_not_active_on_home(self, client) -> None:
        body = client.get("/").content.decode()
        assert_missing_class(
            find_anchor(body, href="/admin/", text="admin"),
            "font-semibold",
        )


class TestAdminSurface:
    """The nested admin layout renders the subnav and the link_card component."""

    def test_admin_shows_recent_links_and_nested_toolbar(self, client) -> None:
        Link.objects.create(slug="xyz789", url="https://example.com/c", clicks=7)
        response = client.get("/admin/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Admin panel" in body
        assert "data-next-link-card" in body
        assert "/s/xyz789/" in body
        assert "7 clicks" in body

    def test_admin_stats_shows_totals(self, client) -> None:
        Link.objects.create(slug="one", url="https://example.com/1", clicks=3)
        Link.objects.create(slug="two", url="https://example.com/2", clicks=4)
        response = client.get("/admin/stats/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Admin panel" in body
        assert "Total clicks" in body
        assert ">7<" in body

    def test_admin_link_detail_inherits_nested_layout(self, client) -> None:
        Link.objects.create(slug="nested", url="https://example.com/n", clicks=2)
        response = client.get("/admin/links/nested/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Admin panel" in body
        assert "Back to links" in body
        assert "Persisted clicks" in body

    def test_home_renders_root_layout_and_link_card(self, client) -> None:
        Link.objects.create(slug="home1", url="https://example.com/home")
        response = client.get("/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "next.dj shortener" in body
        assert "data-next-link-card" in body
        assert "/s/home1/" in body
