from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from django.test import override_settings
from notes.models import Note, Tenant


pytestmark = pytest.mark.django_db


if TYPE_CHECKING:
    from next.testing import NextClient


@pytest.fixture()
def acme(demo_data) -> Tenant:
    return Tenant.objects.get(slug="acme")


@pytest.fixture()
def globex(demo_data) -> Tenant:
    return Tenant.objects.get(slug="globex")


@pytest.fixture()
def acme_note(acme: Tenant) -> Note:
    return Note.objects.get(tenant=acme, title="Welcome to Acme")


@pytest.fixture()
def locked_acme_note(acme: Tenant) -> Note:
    return Note.objects.get(tenant=acme, title="Status update")


class TestTenantContract:
    """Production contract is the X-Tenant header."""

    @override_settings(DEBUG=False)
    def test_missing_header_returns_400(self, next_client: NextClient) -> None:
        response = next_client.get("/notes/")
        assert response.status_code == 400

    @pytest.mark.parametrize(
        ("slug", "visible", "hidden"),
        [
            ("acme", "Welcome to Acme", "Globex roadmap"),
            ("globex", "Globex roadmap", "Welcome to Acme"),
        ],
        ids=["acme", "globex"],
    )
    @override_settings(DEBUG=False)
    def test_header_lists_only_that_tenants_notes(
        self, next_client: NextClient, demo_data, slug, visible, hidden
    ) -> None:
        response = next_client.get("/notes/", HTTP_X_TENANT=slug)
        assert response.status_code == 200
        body = response.content.decode()
        assert visible in body
        assert hidden not in body

    @override_settings(DEBUG=False)
    def test_landing_page_renders_with_recent_notes(
        self, next_client: NextClient, acme: Tenant
    ) -> None:
        """`recent_notes` populates the landing card for the active tenant."""
        response = next_client.get("/", HTTP_X_TENANT="acme")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Welcome to Acme" in body


class TestTenantTheme:
    """The tenant_theme context processor surfaces the primary color."""

    @pytest.mark.parametrize(
        ("slug", "accent"),
        [("acme", "#2563eb"), ("globex", "#16a34a")],
        ids=["acme", "globex"],
    )
    @override_settings(DEBUG=False)
    def test_tenant_color_present_in_html(
        self, next_client: NextClient, demo_data, slug, accent
    ) -> None:
        response = next_client.get("/notes/", HTTP_X_TENANT=slug)
        body = response.content.decode()
        assert f"--tenant-accent:{accent}" in body


class TestTenantPrefixStatic:
    """Static asset URLs are rewritten with the per-tenant prefix."""

    @pytest.mark.parametrize("slug", ["acme", "globex"])
    @override_settings(DEBUG=False)
    def test_static_urls_carry_the_tenant_prefix(
        self, next_client: NextClient, demo_data, slug
    ) -> None:
        response = next_client.get("/notes/", HTTP_X_TENANT=slug)
        body = response.content.decode()
        assert f"/_t/{slug}/static/next/" in body


class TestRootBlocks:
    """Header from `root_blocks/` renders the tenant name."""

    @override_settings(DEBUG=False)
    def test_header_carries_tenant_name(
        self, next_client: NextClient, acme: Tenant
    ) -> None:
        response = next_client.get("/notes/", HTTP_X_TENANT="acme")
        body = response.content.decode()
        assert "Acme Industries" in body
        assert 'class="rounded-full' in body


class TestNoteEditForm:
    """The note_edit form updates the body and respects tenant isolation."""

    @override_settings(DEBUG=False)
    def test_acme_can_save_their_own_note(
        self, next_client: NextClient, acme_note: Note
    ) -> None:
        response = next_client.post_action(
            "note_edit_form",
            {"title": acme_note.title, "body": "edited body content"},
            origin=f"/notes/{acme_note.pk}/edit/",
            HTTP_X_TENANT="acme",
        )
        assert response.status_code == 302
        acme_note.refresh_from_db()
        assert acme_note.body == "edited body content"

    @override_settings(DEBUG=False)
    def test_globex_cannot_edit_acme_note(
        self, next_client: NextClient, acme_note: Note
    ) -> None:
        response = next_client.post_action(
            "note_edit_form",
            {"title": "hijack", "body": "should not save"},
            origin=f"/notes/{acme_note.pk}/edit/",
            HTTP_X_TENANT="globex",
        )
        assert response.status_code == 404
        acme_note.refresh_from_db()
        assert acme_note.title != "hijack"


class TestNoteEditFormPermissionHooks:
    """The dynamic view and object hooks gate the edit form beyond the 404."""

    @override_settings(DEBUG=False)
    def test_active_tenant_edits_unlocked_note(
        self, next_client: NextClient, acme_note: Note
    ) -> None:
        response = next_client.post_action(
            "note_edit_form",
            {"title": acme_note.title, "body": "passed both hooks"},
            origin=f"/notes/{acme_note.pk}/edit/",
            HTTP_X_TENANT="acme",
        )
        assert response.status_code == 302
        acme_note.refresh_from_db()
        assert acme_note.body == "passed both hooks"

    @override_settings(DEBUG=False)
    def test_locked_note_is_denied_with_403(
        self, next_client: NextClient, locked_acme_note: Note
    ) -> None:
        response = next_client.post_action(
            "note_edit_form",
            {"title": locked_acme_note.title, "body": "should not persist"},
            origin=f"/notes/{locked_acme_note.pk}/edit/",
            HTTP_X_TENANT="acme",
        )
        assert response.status_code == 403
        locked_acme_note.refresh_from_db()
        assert locked_acme_note.body != "should not persist"

    @override_settings(DEBUG=False)
    def test_locked_note_editor_announces_the_denial_up_front(
        self, next_client: NextClient, locked_acme_note: Note
    ) -> None:
        response = next_client.get(
            f"/notes/{locked_acme_note.pk}/edit/", HTTP_X_TENANT="acme"
        )
        body = response.content.decode()
        assert "Locked note" in body
        assert re.search(r"<button[^>]*\sdisabled(?=[\s>])", body) is not None

    @override_settings(DEBUG=False)
    def test_unlocked_note_editor_keeps_the_save_button_live(
        self, next_client: NextClient, acme_note: Note
    ) -> None:
        response = next_client.get(f"/notes/{acme_note.pk}/edit/", HTTP_X_TENANT="acme")
        body = response.content.decode()
        assert "Locked note" not in body
        assert re.search(r"<button[^>]*\sdisabled(?=[\s>])", body) is None

    @override_settings(DEBUG=False)
    def test_suspended_tenant_is_denied_with_403(
        self, next_client: NextClient, acme: Tenant, acme_note: Note
    ) -> None:
        Tenant.objects.filter(pk=acme.pk).update(is_active=False)
        response = next_client.post_action(
            "note_edit_form",
            {"title": acme_note.title, "body": "tenant is suspended"},
            origin=f"/notes/{acme_note.pk}/edit/",
            HTTP_X_TENANT="acme",
        )
        assert response.status_code == 403
        acme_note.refresh_from_db()
        assert acme_note.body != "tenant is suspended"

    @override_settings(DEBUG=False)
    def test_view_hook_denies_before_get_initial_404(
        self, next_client: NextClient, acme_note: Note, globex: Tenant
    ) -> None:
        Tenant.objects.filter(pk=globex.pk).update(is_active=False)
        response = next_client.post_action(
            "note_edit_form",
            {"title": "hijack", "body": "cross tenant"},
            origin=f"/notes/{acme_note.pk}/edit/",
            HTTP_X_TENANT="globex",
        )
        assert response.status_code == 403
        acme_note.refresh_from_db()
        assert acme_note.title != "hijack"


class TestNoteEditFormErrorRerender:
    """Invalid POSTs render the page with placeholders replaced and prefix applied."""

    @override_settings(DEBUG=False)
    def test_invalid_submit_keeps_static_pipeline(
        self, next_client: NextClient, acme_note: Note
    ) -> None:
        response = next_client.post_action(
            "note_edit_form",
            {"title": "", "body": "x"},
            origin=f"/notes/{acme_note.pk}/edit/",
            HTTP_X_TENANT="acme",
        )
        body = response.content.decode()
        assert response.status_code == 200
        assert "<!-- next:styles -->" not in body
        assert "<!-- next:scripts -->" not in body
        assert "/_t/acme/static/next/" in body


class TestNoteCreate:
    """The note_create form materialises a new tenant-scoped note."""

    @override_settings(DEBUG=False)
    def test_new_page_renders_empty_form(
        self, next_client: NextClient, acme: Tenant
    ) -> None:
        response = next_client.get("/notes/new/", HTTP_X_TENANT="acme")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Create note" in body
        assert "Preview" in body

    @override_settings(DEBUG=False)
    def test_post_creates_note_and_redirects_to_edit(
        self, next_client: NextClient, acme: Tenant
    ) -> None:
        existing = set(Note.objects.filter(tenant=acme).values_list("pk", flat=True))
        response = next_client.post_action(
            "note_create_form",
            {"title": "Fresh idea", "body": "## body"},
            HTTP_X_TENANT="acme",
        )
        assert response.status_code == 302
        new_pk = next(
            iter(
                set(Note.objects.filter(tenant=acme).values_list("pk", flat=True))
                - existing
            )
        )
        assert response.url == f"/notes/{new_pk}/edit/"
        created = Note.objects.get(pk=new_pk)
        assert created.title == "Fresh idea"
        assert created.body == "## body"

    @override_settings(DEBUG=False)
    def test_create_is_tenant_scoped(
        self, next_client: NextClient, acme: Tenant, globex: Tenant
    ) -> None:
        next_client.post_action(
            "note_create_form",
            {"title": "Globex only", "body": ""},
            HTTP_X_TENANT="globex",
        )
        assert Note.objects.filter(tenant=acme, title="Globex only").count() == 0
        assert Note.objects.filter(tenant=globex, title="Globex only").count() == 1


class TestTenantStaticServe:
    """The `/_t/<slug>/static/...` URL forwards to Django staticfiles."""

    @override_settings(DEBUG=True)
    def test_tenant_static_url_serves_collected_asset(
        self, next_client: NextClient, acme: Tenant
    ) -> None:
        response = next_client.get("/_t/acme/static/next/components/header.css")
        assert response.status_code == 200
        body = b"".join(response.streaming_content)
        assert len(body) > 0

    @override_settings(DEBUG=True)
    def test_tenant_static_url_works_without_tenant_header(
        self, next_client: NextClient, db
    ) -> None:
        """Static path bypasses TenantMiddleware so no header is required."""
        response = next_client.get("/_t/acme/static/next/components/header.css")
        assert response.status_code == 200


class TestNoteEditPage:
    """GET on the edit page seeds the form and renders the markdown preview."""

    @override_settings(DEBUG=False)
    def test_edit_page_renders_with_seeded_form(
        self, next_client: NextClient, acme_note: Note
    ) -> None:
        response = next_client.get(f"/notes/{acme_note.pk}/edit/", HTTP_X_TENANT="acme")
        assert response.status_code == 200
        body = response.content.decode()
        assert acme_note.title in body
        assert "Preview" in body

    @override_settings(DEBUG=False)
    def test_edit_page_prefixes_component_module(
        self, next_client: NextClient, acme_note: Note
    ) -> None:
        response = next_client.get(f"/notes/{acme_note.pk}/edit/", HTTP_X_TENANT="acme")
        body = response.content.decode()
        assert (
            '<script type="module" '
            'src="/_t/acme/static/next/components/markdown_preview.mjs">' in body
        )
        assert 'src="/static/next/components/markdown_preview.mjs"' not in body


class TestDebugAffordance:
    """Browser demo path: ?tenant=<slug> sets a cookie and redirects."""

    @override_settings(DEBUG=True)
    def test_query_param_redirects_with_cookie(
        self, next_client: NextClient, acme: Tenant
    ) -> None:
        response = next_client.get("/notes/?tenant=acme")
        assert response.status_code == 302
        assert response.url == "/notes/"
        assert response.cookies["next_tenant"].value == "acme"

    @override_settings(DEBUG=True)
    def test_query_param_preserves_other_query_params(
        self, next_client: NextClient, acme: Tenant
    ) -> None:
        response = next_client.get("/notes/?tenant=acme&keep=1")
        assert response.status_code == 302
        assert response.url == "/notes/?keep=1"

    @override_settings(DEBUG=True)
    def test_debug_without_any_tenant_returns_400(
        self, next_client: NextClient, db
    ) -> None:
        response = next_client.get("/notes/")
        assert response.status_code == 400

    @override_settings(DEBUG=True)
    def test_cookie_lets_subsequent_requests_pass_without_header(
        self, next_client: NextClient, acme: Tenant
    ) -> None:
        next_client.cookies["next_tenant"] = "acme"
        response = next_client.get("/notes/")
        assert response.status_code == 200
        assert "Welcome to Acme" in response.content.decode()

    @override_settings(DEBUG=False)
    def test_cookie_ignored_in_production(
        self, next_client: NextClient, acme: Tenant
    ) -> None:
        next_client.cookies["next_tenant"] = "acme"
        response = next_client.get("/notes/")
        assert response.status_code == 400
