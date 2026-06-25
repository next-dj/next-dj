from __future__ import annotations

import io

from django.core.cache import cache
from django.core.management import call_command
from shortener.cache import CLICK_PREFIX, increment_clicks, pending_clicks
from shortener.models import Link

from next.testing import (
    assert_has_class,
    assert_missing_class,
    envelope_of,
    find_anchor,
)


class TestShorten:
    """Submitting the form creates a Link and redirects home."""

    def test_post_creates_link_and_redirects(self, client) -> None:
        response = client.post_action(
            "create_link_form", {"url": "https://example.com/a"}
        )
        assert response.status_code == 302
        assert response["Location"] == "/"
        assert Link.objects.count() == 1

    def test_success_message_flashes_on_home(self, client) -> None:
        response = client.post_action(
            "create_link_form",
            {"url": "https://example.com/a"},
            follow=True,
        )
        body = response.content.decode()
        assert "Short link created for https://example.com/a." in body

    def test_invalid_url_renders_form_with_errors(self, client) -> None:
        response = client.post_action(
            "create_link_form",
            {"url": "not-a-url"},
            origin="/",
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


class TestAdminInlineEdit:
    """Each admin row carries a per-link inline edit form keyed by slug."""

    def test_rows_render_edit_forms_keyed_by_slug(self, client) -> None:
        Link.objects.create(slug="alpha", url="https://example.com/a")
        Link.objects.create(slug="bravo", url="https://example.com/b")
        body = client.get("/admin/").content.decode()
        assert 'data-next-key="alpha"' in body
        assert 'data-next-key="bravo"' in body
        assert 'value="https://example.com/a"' in body

    def test_inline_edit_saves_the_addressed_link(self, client) -> None:
        Link.objects.create(slug="alpha", url="https://example.com/a")
        Link.objects.create(slug="bravo", url="https://example.com/b")
        response = client.post_action(
            "edit_link_form",
            {"slug": "bravo", "url": "https://example.com/updated"},
            origin="/admin/",
        )
        assert response.status_code == 302
        assert Link.objects.get(slug="bravo").url == "https://example.com/updated"
        assert Link.objects.get(slug="alpha").url == "https://example.com/a"

    def test_inline_edit_invalid_keeps_the_link(self, client) -> None:
        Link.objects.create(slug="alpha", url="https://example.com/a")
        response = client.post_action(
            "edit_link_form",
            {"slug": "alpha", "url": "not-a-url"},
            origin="/admin/",
        )
        assert response.status_code == 200
        assert b"Enter a valid URL" in response.content
        assert Link.objects.get(slug="alpha").url == "https://example.com/a"

    def test_invalid_partial_edit_morphs_the_keyed_row_form(self, client) -> None:
        Link.objects.create(slug="alpha", url="https://example.com/a")
        Link.objects.create(slug="bravo", url="https://example.com/b")
        response = client.post_action(
            "edit_link_form",
            {"slug": "bravo", "url": "not-a-url"},
            origin="/admin/",
            partial=True,
        )
        assert response.status_code == 200
        assert response["X-Next-Form"] == "invalid"
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        meta = envelope.form_meta()
        assert meta is not None
        assert response["X-Next-Action"] == meta["uid"]
        assert envelope.targets() == [{"form": meta["uid"]}]
        assert meta["valid"] is False
        assert meta["errors"]["url"] == ["Enter a valid URL."]
        html = envelope.ops[0]["html"]
        assert 'data-next-key="bravo"' in html
        assert Link.objects.get(slug="bravo").url == "https://example.com/b"


class TestDeleteRemovesRow:
    """Deleting an admin link patches its keyed row out of the list."""

    def test_partial_delete_removes_the_addressed_row(self, client) -> None:
        Link.objects.create(slug="alpha", url="https://example.com/a")
        Link.objects.create(slug="bravo", url="https://example.com/b")
        response = client.post_action(
            "delete_link",
            {"slug": "bravo"},
            origin="/admin/",
            partial=True,
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["remove"]
        assert envelope.targets() == [{"css": 'li[data-next-key="bravo"]'}]
        assert not Link.objects.filter(slug="bravo").exists()
        assert Link.objects.filter(slug="alpha").exists()

    def test_no_runtime_delete_redirects_to_admin(self, client) -> None:
        Link.objects.create(slug="alpha", url="https://example.com/a")
        response = client.post_action(
            "delete_link", {"slug": "alpha"}, origin="/admin/"
        )
        assert response.status_code == 303
        assert response["Location"] == "/admin/"
        assert not Link.objects.filter(slug="alpha").exists()

    def test_delete_unknown_slug_returns_404(self, client) -> None:
        response = client.post_action(
            "delete_link", {"slug": "ghost"}, origin="/admin/", partial=True
        )
        assert response.status_code == 404


class TestCreatePrependsRow:
    """Creating a link prepends its keyed row to the latest-links list."""

    def test_partial_create_prepends_a_keyed_row(self, client) -> None:
        response = client.post_action(
            "create_link_form",
            {"url": "https://example.com/new"},
            origin="/",
            partial=True,
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["prepend"]
        op = envelope.ops[0]
        assert op["target"] == {"zone": "latest-links"}
        assert op["dedupe"] == "key"
        link = Link.objects.get(url="https://example.com/new")
        assert f'data-next-key="{link.slug}"' in op["html"]
        assert "data-next-link-card" in op["html"]

    def test_no_runtime_create_redirects_home(self, client) -> None:
        response = client.post_action(
            "create_link_form", {"url": "https://example.com/a"}, origin="/"
        )
        assert response.status_code == 302
        assert response["Location"] == "/"
        assert Link.objects.filter(url="https://example.com/a").exists()


class TestResetClicksMorphsForeignBadge:
    """Resetting clicks from the detail page morphs the home badge zone OOB."""

    def test_partial_reset_morphs_the_home_links_badge_zone(self, client) -> None:
        link = Link.objects.create(slug="hot", url="https://example.com/h")
        Link.objects.create(slug="cold", url="https://example.com/c")
        increment_clicks("hot")
        increment_clicks("cold")
        response = client.post_action(
            "reset_clicks",
            {},
            origin=f"/admin/links/{link.slug}/",
            partial=True,
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.zone_targets() == ["links-badge"]
        assert cache.get(f"{CLICK_PREFIX}hot") is None
        assert "1 pending clicks" in envelope.html_for_zone("links-badge")

    def test_no_runtime_reset_redirects_to_detail(self, client) -> None:
        link = Link.objects.create(slug="hot", url="https://example.com/h")
        increment_clicks("hot")
        response = client.post_action(
            "reset_clicks", {}, origin=f"/admin/links/{link.slug}/"
        )
        assert response.status_code == 303
        assert response["Location"] == f"/admin/links/{link.slug}/"
        assert cache.get(f"{CLICK_PREFIX}hot") is None
