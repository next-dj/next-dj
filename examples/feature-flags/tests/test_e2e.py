from __future__ import annotations

import re

import pytest
from django.core.cache import cache
from flags.cache import FLAG_PREFIX, get_cached_flag
from flags.models import Flag
from flags.providers import WRITE_GATE_FLAG

from next.testing import assert_has_class, assert_missing_class, find_anchor


pytestmark = pytest.mark.django_db


class TestSeededDatabase:
    """The seeded flags leave every page populated and the toggle gate open."""

    def test_home_lists_the_seeded_flags(self, next_client, demo_data) -> None:
        body = next_client.get("/").content.decode()
        assert "beta_checkout" in body
        assert "dark_sidebar" in body
        assert "No flags are enabled" not in body
        assert "Nothing disabled" not in body

    def test_admin_lists_every_seeded_flag(self, next_client, demo_data) -> None:
        body = next_client.get("/admin/").content.decode()
        assert "No flags defined yet" not in body
        for name in ("beta_checkout", "dark_sidebar", "ai_suggestions", "admin_writes"):
            assert f'value="{name}"' in body

    def test_demo_shows_the_seeded_guarded_content(
        self, next_client, demo_data
    ) -> None:
        body = next_client.get("/demo/").content.decode()
        assert 'data-feature-guard="beta_checkout"' in body
        assert 'data-feature-guard="dark_sidebar"' not in body

    def test_bulk_toggle_is_allowed_out_of_the_box(
        self, next_client, demo_data
    ) -> None:
        response = next_client.post_action(
            "bulk_toggle_form", {"enabled_names": [WRITE_GATE_FLAG, "dark_sidebar"]}
        )

        assert response.status_code == 302
        assert Flag.objects.get(name="dark_sidebar").enabled is True
        assert Flag.objects.get(name="beta_checkout").enabled is False


class TestHome:
    """The index lists enabled and disabled flags in two columns."""

    def test_enabled_and_disabled_are_partitioned(self, next_client, make_flag) -> None:
        make_flag("on_flag", label="Enabled", enabled=True)
        make_flag("off_flag", label="Disabled", enabled=False)
        response = next_client.get("/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Enabled" in body
        assert "Disabled" in body
        assert "on_flag" in body
        assert "off_flag" in body

    def test_empty_state_renders_both_placeholders(self, next_client) -> None:
        response = next_client.get("/")
        body = response.content.decode()
        assert "No flags are enabled" in body
        assert "Nothing disabled" in body


class TestAdminBulkToggle:
    """Bulk-toggle form updates flags and invalidates each cached entry on save."""

    def test_admin_renders_form_with_checkboxes(self, next_client, make_flag) -> None:
        make_flag("beta", label="Beta")
        response = next_client.get("/admin/")
        body = response.content.decode()
        assert "Flag administration" in body
        assert 'name="enabled_names"' in body
        assert 'value="beta"' in body
        assert "data-next-form" in body or 'method="post"' in body.lower()

    def test_admin_shows_on_and_off_toggle_preview(
        self, next_client, make_flag
    ) -> None:
        make_flag("on_flag", label="On", enabled=True)
        make_flag("off_flag", label="Off")
        response = next_client.get("/admin/")
        body = response.content.decode()
        assert "bg-emerald-100" in body
        assert "bg-slate-100" in body

    def test_posting_toggles_on_and_off(
        self, next_client, write_gate, make_flag
    ) -> None:
        write_gate(enabled=True)
        make_flag("beta", label="Beta")
        make_flag("alpha", label="Alpha", enabled=True)

        response = next_client.post_action(
            "bulk_toggle_form", {"enabled_names": ["beta"]}
        )

        assert response.status_code == 302
        assert response["Location"] == "/admin/"
        assert Flag.objects.get(name="beta").enabled is True
        assert Flag.objects.get(name="alpha").enabled is False

    def test_save_flashes_success_message(
        self, next_client, write_gate, make_flag
    ) -> None:
        write_gate(enabled=True)
        make_flag("beta", label="Beta")

        response = next_client.post_action(
            "bulk_toggle_form", {"enabled_names": ["beta"]}, follow=True
        )

        assert "Flag toggles saved." in response.content.decode()

    def test_save_invalidates_cache(self, next_client, write_gate, make_flag) -> None:
        write_gate(enabled=True)
        make_flag("beta", label="Beta", enabled=True)
        assert get_cached_flag("beta").enabled is True
        assert cache.get(f"{FLAG_PREFIX}beta") is not None

        next_client.post_action("bulk_toggle_form", {"enabled_names": []})

        assert cache.get(f"{FLAG_PREFIX}beta") is None
        assert get_cached_flag("beta").enabled is False

    def test_empty_admin_shows_empty_state(self, next_client) -> None:
        response = next_client.get("/admin/")
        body = response.content.decode()
        assert "No flags defined yet" in body

    def test_unchanged_flag_is_not_resaved(
        self, next_client, write_gate, make_flag
    ) -> None:
        write_gate(enabled=True)
        flag = make_flag("beta", label="Beta", enabled=True)
        original_updated = flag.updated_at

        next_client.post_action("bulk_toggle_form", {"enabled_names": ["beta"]})

        flag.refresh_from_db()
        assert flag.updated_at == original_updated


class TestWriteGate:
    """The check_permissions hook gates the toggle action on the admin_writes flag."""

    def test_gate_off_denies_toggle(self, next_client, write_gate, make_flag) -> None:
        write_gate(enabled=False)
        make_flag("beta", label="Beta")

        response = next_client.post_action(
            "bulk_toggle_form", {"enabled_names": ["beta"]}
        )

        assert response.status_code == 403
        assert Flag.objects.get(name="beta").enabled is False

    def test_gate_absent_denies_toggle(self, next_client, make_flag) -> None:
        make_flag("beta", label="Beta", enabled=True)

        response = next_client.post_action("bulk_toggle_form", {"enabled_names": []})

        assert response.status_code == 403
        assert Flag.objects.get(name="beta").enabled is True

    def test_gate_on_allows_toggle(self, next_client, write_gate, make_flag) -> None:
        write_gate(enabled=True)
        make_flag("beta", label="Beta")

        response = next_client.post_action(
            "bulk_toggle_form", {"enabled_names": ["beta"]}
        )

        assert response.status_code == 302
        assert Flag.objects.get(name="beta").enabled is True

    def test_denial_is_counted_on_metrics_page(
        self, next_client, write_gate, make_flag
    ) -> None:
        write_gate(enabled=False)
        make_flag("beta", label="Beta")
        next_client.post_action("bulk_toggle_form", {"enabled_names": ["beta"]})

        body = next_client.get("/admin/metrics/").content.decode()
        assert "form permission denials" in body
        denial_card = body.split("form permission denials</p>", 1)[1]
        assert re.match(r"\s*<p[^>]*>\s*1\s*</p>", denial_card)


class TestDemoPage:
    """The demo page renders `feature_guard` components for several flags."""

    def test_enabled_flag_renders_banner(self, next_client, make_flag) -> None:
        make_flag(
            "beta_checkout",
            label="Beta checkout",
            description="Use the new checkout flow.",
            enabled=True,
        )
        response = next_client.get("/demo/")
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-feature-guard="beta_checkout"' in body
        assert "Beta checkout" in body
        assert "Use the new checkout flow." in body

    def test_disabled_flag_renders_empty(self, next_client, make_flag) -> None:
        make_flag("beta_checkout", label="Beta")
        response = next_client.get("/demo/")
        body = response.content.decode()
        assert 'data-feature-guard="beta_checkout"' not in body

    def test_unknown_flag_is_treated_as_disabled(self, next_client) -> None:
        Flag.objects.filter(name="ai_suggestions").delete()
        response = next_client.get("/demo/")
        body = response.content.decode()
        assert 'data-feature-guard="ai_suggestions"' not in body

    def test_enabled_without_description_falls_back(
        self, next_client, make_flag
    ) -> None:
        make_flag("dark_sidebar", label="Dark sidebar", enabled=True)
        response = next_client.get("/demo/")
        body = response.content.decode()
        assert "No description provided." in body

    def test_demo_lists_all_known_flag_states(self, next_client, make_flag) -> None:
        make_flag("beta_checkout", label="Beta checkout", enabled=True)
        response = next_client.get("/demo/")
        body = response.content.decode()
        assert "beta_checkout" in body
        assert "dark_sidebar" in body
        assert "ai_suggestions" in body


class TestMetricsPage:
    """The page_rendered receiver records counts visible on the metrics page."""

    def test_metrics_page_shows_per_page_counts(self, next_client) -> None:
        next_client.get("/")
        next_client.get("/")
        next_client.get("/admin/")
        next_client.get("/admin/metrics/")
        response = next_client.get("/admin/metrics/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Renders" in body
        assert ">/<" in body
        assert ">admin<" in body
        assert ">admin/metrics<" in body

    def test_metrics_empty_state(self, next_client) -> None:
        response = next_client.get("/admin/metrics/")
        body = response.content.decode()
        assert "No renders recorded yet." in body


class TestActiveNav:
    """The shared `nav_link` component highlights the current section."""

    def test_admin_link_is_active_on_admin_metrics(self, next_client) -> None:
        body = next_client.get("/admin/metrics/").content.decode()
        assert_has_class(
            find_anchor(body, href="/admin/", text="Admin"), "font-semibold"
        )

    def test_admin_link_not_active_on_home(self, next_client) -> None:
        body = next_client.get("/").content.decode()
        assert_missing_class(
            find_anchor(body, href="/admin/", text="Admin"), "font-semibold"
        )

    def test_admin_subnav_metrics_active_only_on_metrics(self, next_client) -> None:
        body = next_client.get("/admin/metrics/").content.decode()
        assert_has_class(
            find_anchor(body, href="/admin/metrics/", text="Render metrics"),
            "font-semibold",
        )
        assert_missing_class(
            find_anchor(body, href="/admin/", text="Flags"), "font-semibold"
        )


class TestPostDeleteReceiver:
    """Deleting a flag invalidates its cache entry too."""

    def test_delete_drops_cached_entry(self, make_flag) -> None:
        flag = make_flag("beta", label="Beta", enabled=True)
        get_cached_flag("beta")
        assert cache.get(f"{FLAG_PREFIX}beta") is not None

        flag.delete()

        assert cache.get(f"{FLAG_PREFIX}beta") is None
