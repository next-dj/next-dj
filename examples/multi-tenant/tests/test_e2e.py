from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from django.test import override_settings
from notes.models import Note, Tenant


if TYPE_CHECKING:
    from next.testing import NextClient


@pytest.fixture()
def acme(db) -> Tenant:
    return Tenant.objects.get(slug="acme")


@pytest.fixture()
def globex(db) -> Tenant:
    return Tenant.objects.get(slug="globex")


def _acme_note(acme: Tenant) -> Note:
    return Note.objects.get(tenant=acme, title="Welcome to Acme")


def _globex_note(globex: Tenant) -> Note:
    return Note.objects.get(tenant=globex, title="Globex roadmap")


class TestTenantContract:
    """Production contract is the X-Tenant header."""

    @override_settings(DEBUG=False)
    def test_missing_header_returns_400(self, client: NextClient) -> None:
        response = client.get("/notes/")
        assert response.status_code == 400

    @override_settings(DEBUG=False)
    def test_acme_header_lists_acme_notes(
        self, client: NextClient, acme: Tenant
    ) -> None:
        response = client.get("/notes/", HTTP_X_TENANT="acme")
        assert response.status_code == 200
        assert "Welcome to Acme" in response.content.decode()
        assert "Globex roadmap" not in response.content.decode()

    @override_settings(DEBUG=False)
    def test_landing_page_renders_with_recent_notes(
        self, client: NextClient, acme: Tenant
    ) -> None:
        """`recent_notes` populates the landing card for the active tenant."""
        response = client.get("/", HTTP_X_TENANT="acme")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Welcome to Acme" in body

    @override_settings(DEBUG=False)
    def test_globex_header_lists_globex_notes(
        self, client: NextClient, globex: Tenant
    ) -> None:
        response = client.get("/notes/", HTTP_X_TENANT="globex")
        body = response.content.decode()
        assert "Globex roadmap" in body
        assert "Welcome to Acme" not in body


class TestTenantTheme:
    """The tenant_theme context processor surfaces the primary color."""

    @override_settings(DEBUG=False)
    def test_acme_color_present_in_html(self, client: NextClient, acme: Tenant) -> None:
        response = client.get("/notes/", HTTP_X_TENANT="acme")
        body = response.content.decode()
        assert "--tenant-accent:#2563eb" in body

    @override_settings(DEBUG=False)
    def test_globex_color_present_in_html(
        self, client: NextClient, globex: Tenant
    ) -> None:
        response = client.get("/notes/", HTTP_X_TENANT="globex")
        body = response.content.decode()
        assert "--tenant-accent:#16a34a" in body


class TestTenantPrefixStatic:
    """Static asset URLs are rewritten with the per-tenant prefix."""

    @override_settings(DEBUG=False)
    def test_acme_prefixes_static_urls(self, client: NextClient, acme: Tenant) -> None:
        response = client.get("/notes/", HTTP_X_TENANT="acme")
        body = response.content.decode()
        assert "/_t/acme/static/next/" in body

    @override_settings(DEBUG=False)
    def test_globex_prefixes_static_urls(
        self, client: NextClient, globex: Tenant
    ) -> None:
        response = client.get("/notes/", HTTP_X_TENANT="globex")
        body = response.content.decode()
        assert "/_t/globex/static/next/" in body


class TestRootBlocks:
    """Header from `root_blocks/` renders the tenant name."""

    @override_settings(DEBUG=False)
    def test_header_carries_tenant_name(self, client: NextClient, acme: Tenant) -> None:
        response = client.get("/notes/", HTTP_X_TENANT="acme")
        body = response.content.decode()
        assert "Acme Industries" in body
        assert 'class="rounded-full' in body


class TestNoteEditForm:
    """The note_edit form updates the body and respects tenant isolation."""

    @override_settings(DEBUG=False)
    def test_acme_can_save_their_own_note(
        self, client: NextClient, acme: Tenant
    ) -> None:
        note = _acme_note(acme)
        response = client.post_action(
            "notes:note_edit",
            {
                "note_id": note.pk,
                "title": note.title,
                "body": "edited body content",
            },
            HTTP_X_TENANT="acme",
        )
        assert response.status_code == 302
        note.refresh_from_db()
        assert note.body == "edited body content"

    @override_settings(DEBUG=False)
    def test_globex_cannot_edit_acme_note(
        self, client: NextClient, acme: Tenant
    ) -> None:
        note = _acme_note(acme)
        response = client.post_action(
            "notes:note_edit",
            {
                "note_id": note.pk,
                "title": "hijack",
                "body": "should not save",
            },
            HTTP_X_TENANT="globex",
        )
        assert response.status_code == 404
        note.refresh_from_db()
        assert note.title != "hijack"


EDIT_PAGE_FILE = (
    Path(__file__).resolve().parent.parent
    / "notes"
    / "workspaces"
    / "notes"
    / "[int:id]"
    / "edit"
    / "page.py"
)


class TestNoteEditFormErrorRerender:
    """Invalid POSTs render the page with placeholders replaced and prefix applied."""

    @override_settings(DEBUG=False)
    def test_invalid_submit_keeps_static_pipeline(
        self, client: NextClient, acme: Tenant
    ) -> None:
        note = _acme_note(acme)
        response = client.post_action(
            "notes:note_edit",
            {
                "note_id": note.pk,
                "title": "",
                "body": "x",
                "_next_form_page": str(EDIT_PAGE_FILE),
                "_url_param_id": str(note.pk),
            },
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
        self, client: NextClient, acme: Tenant
    ) -> None:
        response = client.get("/notes/new/", HTTP_X_TENANT="acme")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Create note" in body
        assert "Preview" in body

    @override_settings(DEBUG=False)
    def test_post_creates_note_and_redirects_to_edit(
        self, client: NextClient, acme: Tenant
    ) -> None:
        existing = set(Note.objects.filter(tenant=acme).values_list("pk", flat=True))
        response = client.post_action(
            "notes:note_create",
            {"title": "Fresh idea", "body": "## body"},
            HTTP_X_TENANT="acme",
        )
        assert response.status_code == 302
        new_pk = next(
            iter(
                set(Note.objects.filter(tenant=acme).values_list("pk", flat=True))
                - existing,
            ),
        )
        assert response.url == f"/notes/{new_pk}/edit/"
        created = Note.objects.get(pk=new_pk)
        assert created.title == "Fresh idea"
        assert created.body == "## body"

    @override_settings(DEBUG=False)
    def test_create_is_tenant_scoped(
        self, client: NextClient, acme: Tenant, globex: Tenant
    ) -> None:
        client.post_action(
            "notes:note_create",
            {"title": "Globex only", "body": ""},
            HTTP_X_TENANT="globex",
        )
        assert Note.objects.filter(tenant=acme, title="Globex only").count() == 0
        assert Note.objects.filter(tenant=globex, title="Globex only").count() == 1


class TestTenantStaticServe:
    """The `/_t/<slug>/static/...` URL forwards to Django staticfiles."""

    @override_settings(DEBUG=True)
    def test_tenant_static_url_serves_collected_asset(
        self, client: NextClient, acme: Tenant
    ) -> None:
        response = client.get("/_t/acme/static/next/components/header.css")
        assert response.status_code == 200
        body = b"".join(response.streaming_content)
        assert b"accent bar" in body or b"Header" in body

    @override_settings(DEBUG=True)
    def test_tenant_static_url_works_without_tenant_header(
        self, client: NextClient, db
    ) -> None:
        """Static path bypasses TenantMiddleware so no header is required."""
        response = client.get("/_t/acme/static/next/components/header.css")
        assert response.status_code == 200


class TestNoteEditPage:
    """GET on the edit page seeds the form and renders the markdown preview."""

    @override_settings(DEBUG=False)
    def test_edit_page_renders_with_seeded_form(
        self, client: NextClient, acme: Tenant
    ) -> None:
        note = _acme_note(acme)
        response = client.get(
            f"/notes/{note.pk}/edit/",
            HTTP_X_TENANT="acme",
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert note.title in body
        assert "Preview" in body


class TestDebugAffordance:
    """Browser demo path: ?tenant=<slug> sets a cookie and redirects."""

    @override_settings(DEBUG=True)
    def test_query_param_redirects_with_cookie(
        self, client: NextClient, acme: Tenant
    ) -> None:
        response = client.get("/notes/?tenant=acme")
        assert response.status_code == 302
        assert response.url == "/notes/"
        assert response.cookies["next_tenant"].value == "acme"

    @override_settings(DEBUG=True)
    def test_query_param_preserves_other_query_params(
        self, client: NextClient, acme: Tenant
    ) -> None:
        response = client.get("/notes/?tenant=acme&keep=1")
        assert response.status_code == 302
        assert response.url == "/notes/?keep=1"

    @override_settings(DEBUG=True)
    def test_debug_without_any_tenant_returns_400(self, client: NextClient, db) -> None:
        response = client.get("/notes/")
        assert response.status_code == 400

    @override_settings(DEBUG=True)
    def test_cookie_lets_subsequent_requests_pass_without_header(
        self, client: NextClient, acme: Tenant
    ) -> None:
        client.cookies["next_tenant"] = "acme"
        response = client.get("/notes/")
        assert response.status_code == 200
        assert "Welcome to Acme" in response.content.decode()

    @override_settings(DEBUG=False)
    def test_cookie_ignored_in_production(
        self, client: NextClient, acme: Tenant
    ) -> None:
        client.cookies["next_tenant"] = "acme"
        response = client.get("/notes/")
        assert response.status_code == 400
