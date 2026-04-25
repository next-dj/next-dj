from __future__ import annotations

from django.core.cache import cache
from flags.cache import FLAG_PREFIX, get_cached_flag
from flags.models import Flag

from next.testing import assert_has_class, assert_missing_class, find_anchor


class TestHome:
    """The index lists enabled and disabled flags in two columns."""

    def test_enabled_and_disabled_are_partitioned(self, client) -> None:
        Flag.objects.create(name="on_flag", label="Enabled", enabled=True)
        Flag.objects.create(name="off_flag", label="Disabled", enabled=False)
        response = client.get("/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Enabled" in body
        assert "Disabled" in body
        assert "on_flag" in body
        assert "off_flag" in body

    def test_empty_state_renders_both_placeholders(self, client) -> None:
        response = client.get("/")
        body = response.content.decode()
        assert "No flags are enabled" in body
        assert "Nothing disabled" in body


class TestAdminBulkToggle:
    """Bulk-toggle form updates flags and invalidates each cached entry on save."""

    def test_admin_renders_form_with_checkboxes(self, client) -> None:
        Flag.objects.create(name="beta", label="Beta", enabled=False)
        response = client.get("/admin/")
        body = response.content.decode()
        assert "Flag administration" in body
        assert 'name="enabled_names"' in body
        assert 'value="beta"' in body
        assert "data-next-form" in body or 'method="post"' in body.lower()

    def test_admin_shows_on_and_off_toggle_preview(self, client) -> None:
        Flag.objects.create(name="on_flag", label="On", enabled=True)
        Flag.objects.create(name="off_flag", label="Off", enabled=False)
        response = client.get("/admin/")
        body = response.content.decode()
        assert "bg-emerald-100" in body
        assert "bg-slate-100" in body

    def test_posting_toggles_on_and_off(self, client) -> None:
        Flag.objects.create(name="beta", label="Beta", enabled=False)
        Flag.objects.create(name="alpha", label="Alpha", enabled=True)

        response = client.post_action("bulk_toggle", {"enabled_names": ["beta"]})

        assert response.status_code == 302
        assert response["Location"] == "/admin/"
        assert Flag.objects.get(name="beta").enabled is True
        assert Flag.objects.get(name="alpha").enabled is False

    def test_save_invalidates_cache(self, client) -> None:
        Flag.objects.create(name="beta", label="Beta", enabled=True)
        assert get_cached_flag("beta").enabled is True
        assert cache.get(f"{FLAG_PREFIX}beta") is not None

        client.post_action("bulk_toggle", {"enabled_names": []})

        assert cache.get(f"{FLAG_PREFIX}beta") is None
        assert get_cached_flag("beta").enabled is False

    def test_empty_admin_shows_empty_state(self, client) -> None:
        response = client.get("/admin/")
        body = response.content.decode()
        assert "No flags defined yet" in body

    def test_unchanged_flag_is_not_resaved(self, client) -> None:
        flag = Flag.objects.create(name="beta", label="Beta", enabled=True)
        original_updated = flag.updated_at

        client.post_action("bulk_toggle", {"enabled_names": ["beta"]})

        flag.refresh_from_db()
        assert flag.updated_at == original_updated


class TestDemoPage:
    """The demo page renders `feature_guard` components for several flags."""

    def test_enabled_flag_renders_banner(self, client) -> None:
        Flag.objects.create(
            name="beta_checkout",
            label="Beta checkout",
            description="Use the new checkout flow.",
            enabled=True,
        )
        response = client.get("/demo/")
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-feature-guard="beta_checkout"' in body
        assert "Beta checkout" in body
        assert "Use the new checkout flow." in body

    def test_disabled_flag_renders_empty(self, client) -> None:
        Flag.objects.create(name="beta_checkout", label="Beta", enabled=False)
        response = client.get("/demo/")
        body = response.content.decode()
        assert 'data-feature-guard="beta_checkout"' not in body

    def test_unknown_flag_is_treated_as_disabled(self, client) -> None:
        response = client.get("/demo/")
        body = response.content.decode()
        assert 'data-feature-guard="ai_suggestions"' not in body

    def test_enabled_without_description_falls_back(self, client) -> None:
        Flag.objects.create(name="dark_sidebar", label="Dark sidebar", enabled=True)
        response = client.get("/demo/")
        body = response.content.decode()
        assert "No description provided." in body

    def test_demo_lists_all_known_flag_states(self, client) -> None:
        Flag.objects.create(name="beta_checkout", label="Beta checkout", enabled=True)
        response = client.get("/demo/")
        body = response.content.decode()
        assert "beta_checkout" in body
        assert "dark_sidebar" in body
        assert "ai_suggestions" in body


class TestMetricsPage:
    """The page_rendered receiver records counts visible on the metrics page."""

    def test_metrics_page_shows_per_page_counts(self, client) -> None:
        client.get("/")
        client.get("/")
        client.get("/admin/")
        client.get("/admin/metrics/")
        response = client.get("/admin/metrics/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Renders" in body
        assert ">/<" in body
        assert ">admin<" in body
        assert ">admin/metrics<" in body

    def test_metrics_empty_state(self, client) -> None:
        response = client.get("/admin/metrics/")
        body = response.content.decode()
        assert "No renders recorded yet." in body


class TestActiveNav:
    """The shared `nav_link` component highlights the current section."""

    def test_admin_link_is_active_on_admin_metrics(self, client) -> None:
        body = client.get("/admin/metrics/").content.decode()
        assert_has_class(
            find_anchor(body, href="/admin/", text="Admin"), "font-semibold"
        )

    def test_admin_link_not_active_on_home(self, client) -> None:
        body = client.get("/").content.decode()
        assert_missing_class(
            find_anchor(body, href="/admin/", text="Admin"),
            "font-semibold",
        )

    def test_admin_subnav_metrics_active_only_on_metrics(self, client) -> None:
        body = client.get("/admin/metrics/").content.decode()
        assert_has_class(
            find_anchor(body, href="/admin/metrics/", text="Render metrics"),
            "font-semibold",
        )
        assert_missing_class(
            find_anchor(body, href="/admin/", text="Flags"),
            "font-semibold",
        )


class TestPostDeleteReceiver:
    """Deleting a flag invalidates its cache entry too."""

    def test_delete_drops_cached_entry(self) -> None:
        flag = Flag.objects.create(name="beta", label="Beta", enabled=True)
        get_cached_flag("beta")
        assert cache.get(f"{FLAG_PREFIX}beta") is not None

        flag.delete()

        assert cache.get(f"{FLAG_PREFIX}beta") is None
