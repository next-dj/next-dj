import re
import warnings
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import pytest
from admin_audit.models import AdminActivityLog
from django.conf import settings
from django.contrib import admin
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.staticfiles import finders
from django.core.checks import run_checks
from django.core.management import call_command
from django.db import connection
from django.db.models import UniqueConstraint
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from library.demo import DEMO_BOOKS, seed_demo
from library.models import Book, Chapter, Tag
from shadcn_admin.forms import AdminFormSpec, AdminInlineSpec, InlineRights

from next.testing import (
    NextClient,
    assert_metadata,
    envelope_of,
    find_anchor,
    find_form,
    form_action,
    form_fields,
    hidden_fields,
)
from next.urls import page_reverse


pytestmark = pytest.mark.django_db

EXAMPLE_DIR = Path(__file__).resolve().parent.parent
STATIC_URL = settings.STATIC_URL
ELEMENT_ID_RE = re.compile(r'\sid="([^"]+)"')
CRUMB_NAV_RE = re.compile(r'<nav aria-label="Breadcrumb"[^>]*>(.*?)</nav>', re.DOTALL)
CRUMB_LINK_RE = re.compile(r'<a href="([^"]*)"( aria-current="page")?[^>]*>([^<]*)</a>')


def action_form(client: NextClient, body: str, action_name: str) -> str:
    """Return the page form that posts `action_name`, found by its dispatch URL."""
    return find_form(body, action=client.get_action_url(action_name))


def crumbs(html: str) -> list[tuple[str, str, bool]]:
    """Return the breadcrumb links of the shell as `(label, href, current)`."""
    nav = CRUMB_NAV_RE.search(html)
    if nav is None:
        return []
    return [
        (label, href, bool(current))
        for href, current, label in CRUMB_LINK_RE.findall(nav.group(1))
    ]


def element_ids(html: str) -> list[str]:
    """Return every `id` attribute in the fragment, in document order."""
    return ELEMENT_ID_RE.findall(html)


class TestDashboard:
    def test_bare_root_redirects_to_admin(self, next_client):
        r = next_client.get("/")
        assert r.status_code == 302
        assert r["Location"] == "/admin/"

    def test_unauthenticated_redirects_to_login(self, next_client):
        r = next_client.get("/admin/")
        assert r.status_code == 302
        assert r["Location"].startswith("/admin/login/")

    def test_authenticated_dashboard_lists_registered_models(self, admin_client):
        r = admin_client.get("/admin/")
        assert r.status_code == 200
        body = r.content.decode()
        for label in ("Library", "Books", "Authors", "Tags", "Chapters"):
            assert label in body

    def test_the_project_layout_produces_one_document(self, admin_client):
        """`chrome/layout.djx` is the only layout, so nothing in the shell repeats."""
        body = admin_client.get("/admin/").content.decode()
        assert body.count("<!DOCTYPE html>") == 1
        assert body.count("<body") == 1
        assert body.count("<aside") == 1
        assert body.count("<main") == 1
        assert body.count("Models registered with django.contrib.admin") == 1

    def test_the_topbar_offers_exactly_one_sign_out_form(self, admin_client):
        body = admin_client.get("/admin/").content.decode()
        logout_url = admin_client.get_action_url("admin:logout")
        assert body.count(f'action="{logout_url}"') == 1
        assert "Sign out" in find_form(body, action=logout_url)

    def test_each_app_reaches_the_sidebar_and_a_dashboard_card(self, admin_client):
        body = admin_client.get("/admin/").content.decode()
        assert body.count("Authentication and Authorization") == 2
        assert body.count(">Library<") == 2


class TestAuth:
    def test_login_page_renders(self, next_client):
        r = next_client.get("/admin/login/")
        assert r.status_code == 200
        body = r.content.decode()
        assert 'name="username"' in body
        assert 'name="password"' in body

    def test_login_post_redirects_and_authenticates(self, next_client, admin_user):
        r = next_client.post_action(
            "admin:login",
            {"username": "admin", "password": "admin-pass", "next": "/admin/"},
        )
        assert r.status_code == 302
        assert r["Location"] == "/admin/"
        r2 = next_client.get("/admin/")
        assert r2.status_code == 200

    def test_logout_clears_session(self, admin_client):
        r = admin_client.post_action("admin:logout", {})
        assert r.status_code == 302
        assert r["Location"] == "/admin/logout/"
        r2 = admin_client.get("/admin/")
        assert r2.status_code == 302
        assert r2["Location"].startswith("/admin/login/")

    def test_logout_lands_on_farewell_page(self, admin_client):
        r = admin_client.post_action("admin:logout", {}, follow=True)
        body = r.content.decode()
        assert "You have been signed out." in body
        assert "Sign in again" in body

    def test_bad_credentials_renders_form_error(self, next_client, admin_user):
        body = next_client.get("/admin/login/").content.decode()
        rendered = form_fields(action_form(next_client, body, "admin:login"))
        r = next_client.post_action(
            "admin:login",
            {**rendered, "username": "admin", "password": "wrong", "next": "/admin/"},
        )
        assert r.status_code == 200
        body = r.content.decode()
        assert "Please enter a correct" in body or "correct username" in body


class TestActionGuards:
    """Mutating actions reject anonymous POSTs and unauthorized users."""

    def test_anonymous_add_post_redirects_to_login(self, next_client):
        r = next_client.post_action(
            "admin:add",
            {"name": "Sneaky", "slug": "sneaky"},
            origin="/admin/library/tag/add/",
        )
        assert r.status_code == 302
        assert r["Location"].startswith("/admin/login/")
        assert not Tag.objects.filter(slug="sneaky").exists()

    def test_anonymous_change_post_redirects_to_login(self, next_client, make_tag):
        tag = make_tag("Old")
        r = next_client.post_action(
            "admin:change",
            {"name": "Hacked", "slug": "hacked"},
            origin=f"/admin/library/tag/{tag.pk}/change/",
        )
        assert r.status_code == 302
        assert r["Location"].startswith("/admin/login/")
        tag.refresh_from_db()
        assert tag.name == "Old"

    def test_anonymous_delete_post_redirects_to_login(self, next_client, make_tag):
        tag = make_tag("Keep")
        r = next_client.post_action(
            "admin:delete", origin=f"/admin/library/tag/{tag.pk}/delete/"
        )
        assert r.status_code == 302
        assert r["Location"].startswith("/admin/login/")
        assert Tag.objects.filter(pk=tag.pk).exists()

    def test_anonymous_bulk_action_post_redirects_to_login(
        self, next_client, make_book
    ):
        book = make_book(status=Book.DRAFT)
        r = next_client.post_action(
            "admin:bulk_action",
            {"action": "mark_as_published", "_selected_action": [str(book.pk)]},
            origin="/admin/library/book/",
        )
        assert r.status_code == 302
        assert r["Location"].startswith("/admin/login/")
        book.refresh_from_db()
        assert book.status == Book.DRAFT

    def test_non_staff_add_post_is_forbidden(self, next_client, django_user_model):
        next_client.force_login(django_user_model.objects.create_user("intruder"))
        r = next_client.post_action(
            "admin:add",
            {"name": "Sneaky", "slug": "sneaky"},
            origin="/admin/library/tag/add/",
        )
        assert r.status_code == 403
        assert not Tag.objects.filter(slug="sneaky").exists()

    def test_non_staff_change_post_is_forbidden(
        self, next_client, django_user_model, make_tag
    ):
        next_client.force_login(django_user_model.objects.create_user("intruder"))
        tag = make_tag("Old")
        r = next_client.post_action(
            "admin:change",
            {"name": "Hacked", "slug": "hacked"},
            origin=f"/admin/library/tag/{tag.pk}/change/",
        )
        assert r.status_code == 403
        tag.refresh_from_db()
        assert tag.name == "Old"

    def test_non_staff_delete_post_is_forbidden(
        self, next_client, django_user_model, make_tag
    ):
        next_client.force_login(django_user_model.objects.create_user("intruder"))
        tag = make_tag("Keep")
        r = next_client.post_action(
            "admin:delete", origin=f"/admin/library/tag/{tag.pk}/delete/"
        )
        assert r.status_code == 403
        assert Tag.objects.filter(pk=tag.pk).exists()

    def test_staff_without_model_rights_cannot_run_a_bulk_action(
        self, editor_client, editor
    ):
        r = editor_client.post_action(
            "admin:bulk_action",
            {"action": "unknown", "_selected_action": [str(editor.pk)]},
            origin="/admin/auth/user/",
        )
        assert r.status_code == 403

    def test_non_staff_bulk_action_post_is_forbidden(
        self, next_client, django_user_model, make_book
    ):
        next_client.force_login(django_user_model.objects.create_user("intruder"))
        book = make_book(status=Book.DRAFT)
        r = next_client.post_action(
            "admin:bulk_action",
            {"action": "mark_as_published", "_selected_action": [str(book.pk)]},
            origin="/admin/library/book/",
        )
        assert r.status_code == 403
        book.refresh_from_db()
        assert book.status == Book.DRAFT


PROTECTED_PATHS = (
    "/admin/",
    "/admin/library/book/",
    "/admin/library/book/add/",
    "/admin/activity/",
    "/admin/library/book/{pk}/history/",
    "/admin/library/book/{pk}/outline/",
    "/admin/library/book/{pk}/manuscript/",
)

DASHBOARD_PANELS = "dashboard-panels"

PERMISSION_CHECKS = frozenset(
    [f"next.E{number}" for number in range(159, 165)]
    + [f"next.W{number}" for number in range(127, 130)]
)

ACTIVITY_ACCESS = (
    "login, perm shadcn_admin.use_admin, perm admin_audit.view_adminactivitylog"
)

LIBRARY_RULE = "library/pages/page.py"
MANUSCRIPT_RULE = "library/pages/library/book/[int:book]/manuscript/page.py"
MANUSCRIPT_ACCESS = (
    "login, perm shadcn_admin.use_admin, perm library.change_book on library.Book"
)


def permission_rows(*args: str) -> dict[str, tuple[str, str, str]]:
    """Return the rows `manage.py permissions` prints, keyed by URL or action name.

    An intercept row keeps its zones in the key, since it shares its target's URL.
    """
    out = StringIO()
    call_command("permissions", *args, stdout=out)
    _header, *lines = out.getvalue().splitlines()
    rows = (re.split(r"\s{2,}", line.strip()) for line in lines)
    return {
        name if kind == "intercept" else name.partition(" ")[0]: (kind, access, sources)
        for kind, name, access, sources in rows
    }


def log_entry(object_repr: str) -> AdminActivityLog:
    return AdminActivityLog.objects.create(
        action="change",
        app_label="library",
        model_name="book",
        object_repr=object_repr,
        response_status=302,
    )


class TestTreeAccess:
    """The `page.py` rules close each admin tree and the auth pages stay public."""

    @pytest.mark.parametrize("path", PROTECTED_PATHS)
    def test_an_anonymous_visit_redirects_to_the_login(self, next_client, book, path):
        url = path.format(pk=book.pk)
        r = next_client.get(url)
        assert r.status_code == 302
        assert r["Location"] == f"/admin/login/?next={url}"

    @pytest.mark.parametrize("path", PROTECTED_PATHS)
    def test_a_signed_in_non_staff_user_is_forbidden(
        self, next_client, django_user_model, book, path
    ):
        next_client.force_login(django_user_model.objects.create_user("reader"))
        assert next_client.get(path.format(pk=book.pk)).status_code == 403

    @pytest.mark.parametrize("path", ["/admin/login/", "/admin/logout/"])
    def test_the_auth_pages_answer_an_anonymous_visit(self, next_client, path):
        assert next_client.get(path).status_code == 200

    def test_an_anonymous_zone_request_redirects_to_the_login(self, next_client):
        r = next_client.get_zones("/admin/", DASHBOARD_PANELS)
        assert r.status_code == 302
        assert r["Location"] == "/admin/login/?next=/admin/"

    def test_an_anonymous_sign_out_post_redirects_to_the_login(self, next_client):
        r = next_client.post_action("admin:logout", origin="/admin/")
        assert r.status_code == 302
        assert r["Location"] == "/admin/login/?next=/admin/"

    def test_the_permission_table_names_each_rule_and_its_file(self):
        rows = permission_rows()
        assert rows["/admin/"] == (
            "page",
            "login, perm shadcn_admin.use_admin",
            "shadcn_admin/pages/page.py",
        )
        assert rows["/admin/activity/"] == (
            "page",
            ACTIVITY_ACCESS,
            "admin_audit/pages/page.py",
        )
        assert rows["/admin/login/"] == (
            "page",
            "public",
            "public (shadcn_admin/pages/(auth)/page.py)",
        )
        assert rows["admin:login"][1] == "public"
        assert rows["admin:logout"][1:] == ("login (default)", "LOGIN_REQUIRED")

    def test_the_permission_table_narrows_to_one_url(self):
        rows = permission_rows("/admin/library/book/1/history/")
        assert rows["/admin/<str:app_label>/<str:model_name>/<int:pk>/history/"] == (
            "page",
            "login, perm shadcn_admin.use_admin",
            "shadcn_admin/pages/page.py",
        )

    def test_the_permission_table_names_the_book_rules_and_their_files(self):
        outline = permission_rows("/admin/library/book/1/outline/")
        assert outline["/admin/library/book/<int:book>/outline/"] == (
            "page",
            "login, perm shadcn_admin.use_admin",
            LIBRARY_RULE,
        )
        manuscript = permission_rows("/admin/library/book/1/manuscript/")
        rule = (MANUSCRIPT_ACCESS, f"{LIBRARY_RULE}, {MANUSCRIPT_RULE}")
        assert manuscript["/admin/library/book/<int:book>/manuscript/"] == (
            "page",
            *rule,
        )
        assert manuscript["add_chapter_form"] == ("action", *rule)


class TestDashboardPanels:
    """`admin_audit` registers a dashboard panel that only readers of the log see."""

    def test_the_superuser_sees_the_newest_entries(self, admin_client):
        log_entry("Frankenstein")
        body = admin_client.get("/admin/").content.decode()
        assert f'data-next-zone="{DASHBOARD_PANELS}"' in body
        assert "Recent activity" in body
        assert "Frankenstein" in body
        assert 'href="/admin/activity/"' in body

    def test_the_zone_shows_an_empty_feed(self, admin_client):
        r = admin_client.get_zones("/admin/", DASHBOARD_PANELS)
        assert "No activity yet." in envelope_of(r).html_for_zone(DASHBOARD_PANELS)

    def test_a_zone_render_carries_an_entry_logged_after_the_page(self, admin_client):
        admin_client.get("/admin/")
        log_entry("Dracula")
        r = admin_client.get_zones("/admin/", DASHBOARD_PANELS)
        assert r.status_code == 200
        assert "Dracula" in envelope_of(r).html_for_zone(DASHBOARD_PANELS)

    def test_the_editor_gets_no_panel(self, editor_client):
        log_entry("Frankenstein")
        body = editor_client.get("/admin/").content.decode()
        assert "Recent activity" not in body
        assert "Frankenstein" not in body

    def test_a_zone_request_from_the_editor_carries_no_entry(self, editor_client):
        log_entry("Frankenstein")
        r = editor_client.get_zones("/admin/", DASHBOARD_PANELS)
        assert r.status_code == 200
        html = envelope_of(r).html_for_zone(DASHBOARD_PANELS)
        assert "Recent activity" not in html
        assert "Frankenstein" not in html

    def test_the_feed_keeps_the_five_newest(self, admin_client):
        for number in range(7):
            log_entry(f"Entry {number}")
        html = envelope_of(
            admin_client.get_zones("/admin/", DASHBOARD_PANELS)
        ).html_for_zone(DASHBOARD_PANELS)
        assert html.count("<li") == 5


OUTLINE = "outline"
MANUSCRIPT = "manuscript"
APPEND = "Append chapter"
READ_ONLY = "data-outline-read-only"


def outline_url(book: Book) -> str:
    return f"/admin/library/book/{book.pk}/outline/"


def manuscript_url(book: Book) -> str:
    return f"/admin/library/book/{book.pk}/manuscript/"


def append(client: NextClient, book: Book, title: str, **kwargs):
    return client.post_action(
        "add_chapter_form",
        {"title": title, "word_count": "300"},
        origin=manuscript_url(book),
        **kwargs,
    )


def book_reads(client: NextClient, url: str) -> int:
    with CaptureQueriesContext(connection) as queries:
        assert client.get(url).status_code == 200
    return sum('FROM "library_book"' in q["sql"] for q in queries.captured_queries)


class TestBookOutline:
    """Every admin reads the outline, and its zone decides who sees the manuscript."""

    def test_the_editor_reads_the_outline_of_a_draft(
        self, editor_client, make_book, make_chapter
    ):
        draft = make_book("Mathilda", status=Book.DRAFT)
        make_chapter(draft, 1, "Intro", 100)
        make_chapter(draft, 2, "Rising", 250)
        r = editor_client.get(outline_url(draft))
        assert r.status_code == 200
        body = r.content.decode()
        assert f'data-next-zone="{OUTLINE}"' in body
        assert body.index("Intro") < body.index("Rising")
        assert "data-total-words>350<" in body
        assert APPEND not in body

    def test_an_empty_draft_says_so(self, editor_client, make_book):
        body = editor_client.get(outline_url(make_book(status=Book.DRAFT))).content
        assert "No chapters yet." in body.decode()

    def test_the_editor_reads_a_published_book_without_the_link(
        self, editor_client, make_book
    ):
        published = make_book("Dracula", status=Book.PUBLISHED)
        r = editor_client.get(outline_url(published))
        assert r.status_code == 200
        body = r.content.decode()
        assert "Dracula" in body
        assert manuscript_url(published) not in body
        assert READ_ONLY in body

    @pytest.mark.parametrize(
        ("client_name", "status", "owner", "links"),
        [
            ("editor_client", Book.DRAFT, "editor", True),
            ("editor_client", Book.DRAFT, None, True),
            ("editor_client", Book.DRAFT, "copyeditor", False),
            ("editor_client", Book.PUBLISHED, "editor", False),
            ("admin_client", Book.PUBLISHED, "copyeditor", True),
        ],
    )
    def test_a_zone_render_decides_who_sees_the_manuscript(
        self, request, make_book, make_chapter, client_name, status, owner, links
    ):
        client = request.getfixturevalue(client_name)
        editor = owner and request.getfixturevalue(owner)
        target = make_book(status=status, editor=editor)
        make_chapter(target, title="Opening")
        r = client.get_zones(outline_url(target), OUTLINE)
        assert r.status_code == 200
        html = envelope_of(r).html_for_zone(OUTLINE)
        assert "Opening" in html
        assert (manuscript_url(target) in html) is links
        assert (READ_ONLY in html) is not links

    def test_an_anonymous_zone_request_redirects_to_the_login(self, next_client, book):
        r = next_client.get_zones(outline_url(book), OUTLINE)
        assert r.status_code == 302
        assert r["Location"] == f"/admin/login/?next={outline_url(book)}"

    def test_the_page_reads_the_book_once(self, editor_client, make_book):
        assert book_reads(editor_client, outline_url(make_book())) == 1

    def test_an_unknown_book_is_not_found(self, admin_client):
        assert admin_client.get("/admin/library/book/999/outline/").status_code == 404

    def test_the_crumbs_start_at_the_dashboard(self, admin_client, book):
        body = admin_client.get(outline_url(book)).content.decode()
        assert crumbs(body) == [
            ("Dashboard", "/admin/", False),
            ("Outline", outline_url(book), True),
        ]

    def test_the_page_is_titled_after_the_book(self, admin_client, make_book):
        target = make_book("Ulysses")
        assert_metadata(
            admin_client.get(outline_url(target)), title="Ulysses · next.dj admin"
        )

    def test_the_outline_links_back_to_the_change_view(self, admin_client, book):
        body = admin_client.get(outline_url(book)).content.decode()
        assert f'href="/admin/library/book/{book.pk}/change/"' in body

    @pytest.mark.parametrize(
        ("client_name", "status"),
        [
            ("editor_client", Book.DRAFT),
            ("editor_client", Book.PUBLISHED),
            ("admin_client", Book.PUBLISHED),
        ],
    )
    def test_the_change_view_links_the_outline(
        self, request, make_book, client_name, status
    ):
        client = request.getfixturevalue(client_name)
        target = make_book(status=status)
        body = client.get(f"/admin/library/book/{target.pk}/change/").content.decode()
        assert f'href="{outline_url(target)}"' in body

    def test_a_model_without_object_links_offers_none(self, admin_client, make_tag):
        tag = make_tag()
        body = admin_client.get(f"/admin/library/tag/{tag.pk}/change/").content
        assert "/outline/" not in body.decode()


class TestManuscript:
    """`manuscript/page.py` asks `change_book` on the book the URL names."""

    def test_the_editor_works_an_own_draft(
        self, editor_client, editor, make_book, make_chapter
    ):
        draft = make_book("Mathilda", status=Book.DRAFT, editor=editor)
        make_chapter(draft, title="Opening")
        r = editor_client.get(manuscript_url(draft))
        assert r.status_code == 200
        body = r.content.decode()
        assert f'data-next-zone="{MANUSCRIPT}"' in body
        assert "Opening" in body
        assert APPEND in body

    def test_an_unassigned_draft_opens_for_every_editor(self, editor_client, make_book):
        draft = make_book(status=Book.DRAFT)
        assert editor_client.get(manuscript_url(draft)).status_code == 200

    @pytest.mark.parametrize(
        ("status", "owner"),
        [(Book.DRAFT, "copyeditor"), (Book.PUBLISHED, "editor")],
        ids=["foreign-draft", "own-published"],
    )
    def test_the_editor_is_refused_on_every_path(
        self, request, editor_client, make_book, make_chapter, status, owner
    ):
        target = make_book(status=status, editor=request.getfixturevalue(owner))
        make_chapter(target, title="Secret")
        url = manuscript_url(target)
        page_response = editor_client.get(url)
        assert page_response.status_code == 403
        assert b"Secret" not in page_response.content
        zone_response = editor_client.get_zones(url, MANUSCRIPT)
        assert zone_response.status_code == 403
        assert b"Secret" not in zone_response.content
        assert append(editor_client, target, "Coda").status_code == 403
        assert list(target.chapters.values_list("title", flat=True)) == ["Secret"]

    def test_the_change_view_of_a_foreign_draft_is_read_only(
        self, editor_client, copyeditor, make_book
    ):
        draft = make_book(status=Book.DRAFT, editor=copyeditor)
        body = editor_client.get(f"/admin/library/book/{draft.pk}/change/").content
        assert "data-read-only" in body.decode()

    def test_a_zone_render_carries_the_form(self, editor_client, editor, make_book):
        draft = make_book(status=Book.DRAFT, editor=editor)
        r = editor_client.get_zones(manuscript_url(draft), MANUSCRIPT)
        assert r.status_code == 200
        assert APPEND in envelope_of(r).html_for_zone(MANUSCRIPT)

    def test_the_editor_appends_a_chapter_to_an_own_draft(
        self, editor_client, editor, make_book, make_chapter
    ):
        draft = make_book(status=Book.DRAFT, editor=editor)
        make_chapter(draft, 1, "Intro", 100)
        r = append(editor_client, draft, "Coda")
        assert r.status_code == 303
        assert r["Location"] == manuscript_url(draft)
        added = draft.chapters.get(title="Coda")
        assert (added.number, added.word_count) == (2, 300)

    def test_a_partial_append_morphs_the_manuscript(self, editor_client, make_book):
        draft = make_book(status=Book.DRAFT)
        r = append(editor_client, draft, "Prologue", partial=True)
        assert r.status_code == 200
        assert envelope_of(r).zone_targets() == [MANUSCRIPT]
        assert "Prologue" in envelope_of(r).html_for_zone(MANUSCRIPT)
        assert draft.chapters.get().number == 1

    def test_a_blank_title_rerenders_with_its_error(self, editor_client, make_book):
        draft = make_book(status=Book.DRAFT)
        r = append(editor_client, draft, "")
        assert r.status_code == 200
        assert "This field is required." in r.content.decode()
        assert not draft.chapters.exists()

    def test_a_partial_blank_title_reports_the_invalid_form(
        self, editor_client, make_book
    ):
        draft = make_book(status=Book.DRAFT)
        r = append(editor_client, draft, "", partial=True)
        assert r["X-Next-Form"] == "invalid"
        assert envelope_of(r).form_meta()["errors"]["title"] == [
            "This field is required."
        ]
        assert not draft.chapters.exists()

    def test_the_superuser_works_a_published_book(
        self, admin_client, editor, make_book
    ):
        published = make_book("Dracula", status=Book.PUBLISHED, editor=editor)
        assert APPEND in admin_client.get(manuscript_url(published)).content.decode()
        assert append(admin_client, published, "Epilogue").status_code == 303
        assert published.chapters.get().title == "Epilogue"

    def test_an_anonymous_append_redirects_to_the_login(self, next_client, book):
        r = append(next_client, book, "Coda")
        assert r.status_code == 302
        assert r["Location"].startswith("/admin/login/")
        assert not book.chapters.exists()

    def test_the_rule_and_the_page_read_the_book_once(self, editor_client, make_book):
        draft = make_book(status=Book.DRAFT)
        assert book_reads(editor_client, manuscript_url(draft)) == 1

    def test_an_unknown_book_is_not_found(self, admin_client):
        r = admin_client.get("/admin/library/book/999/manuscript/")
        assert r.status_code == 404

    def test_the_crumbs_end_at_the_manuscript(self, admin_client, book):
        body = admin_client.get(manuscript_url(book)).content.decode()
        assert crumbs(body) == [
            ("Dashboard", "/admin/", False),
            ("Manuscript", manuscript_url(book), True),
        ]

    def test_the_manuscript_links_back_to_the_outline(self, admin_client, book):
        body = admin_client.get(manuscript_url(book)).content.decode()
        assert f'href="{outline_url(book)}"' in body


class TestDemoSeed:
    """The demo catalog loads from a seed module, never from a migration."""

    def test_command_fills_the_catalog(self, db):
        call_command("seed_demo")
        assert Book.objects.count() == len(DEMO_BOOKS)
        assert Chapter.objects.filter(book__title="Frankenstein").count() == 4

    def test_seeding_twice_keeps_one_copy(self, demo_data):
        seed_demo()
        assert Book.objects.count() == len(DEMO_BOOKS)

    def test_the_books_split_between_two_editors(self, demo_data):
        assert set(Book.objects.values_list("editor__username", flat=True)) == {
            "editor",
            "copyeditor",
        }
        assert Book.objects.get(title="Mathilda").editor.username == "editor"
        mysterious = Book.objects.get(title="The Mysterious Island")
        assert mysterious.editor.username == "copyeditor"


class TestSeededCatalog:
    """`library/demo.py` fills every changelist feature once seeded."""

    def test_book_changelist_is_populated_without_manual_entry(
        self, admin_client, demo_data
    ):
        r = admin_client.get("/admin/library/book/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "Frankenstein" in body
        assert "Dracula" in body
        assert "Mary Shelley" in body

    def test_book_changelist_spans_more_than_one_page(self, admin_client, demo_data):
        r = admin_client.get("/admin/library/book/")
        body = r.content.decode()
        assert "19 items" in body
        assert "page 1 of 2" in body
        assert "?p=2" in body
        second = admin_client.get("/admin/library/book/?p=2")
        assert second.status_code == 200
        assert "The Invisible Man" in second.content.decode()

    @pytest.mark.parametrize(
        ("query", "title"),
        [
            ("status__exact=draft", "The Invisible Man"),
            ("status__exact=published", "Dracula"),
            ("status__exact=archived", "Villette"),
            ("is_featured__exact=1", "The Time Machine"),
            ("is_featured__exact=0", "Emma"),
            ("tags__isnull=True", "Mathilda"),
            ("q=Verne", "Around the World in Eighty Days"),
        ],
        ids=(
            "status_draft",
            "status_published",
            "status_archived",
            "featured_only",
            "unfeatured_only",
            "untagged_only",
            "search_matches_author_name",
        ),
    )
    def test_every_advertised_facet_returns_rows(
        self, admin_client, demo_data, query, title
    ):
        r = admin_client.get(f"/admin/library/book/?{query}")
        assert r.status_code == 200
        assert title in r.content.decode()

    def test_a_seeded_book_opens_with_a_filled_inline(self, admin_client, demo_data):
        book = Book.objects.get(title="Frankenstein")
        r = admin_client.get(f"/admin/library/book/{book.pk}/change/")
        assert r.status_code == 200
        body = r.content.decode()
        assert 'value="Ingolstadt"' in body
        assert 'value="The creature speaks"' in body


class TestChangelist:
    def test_changelist_lists_rows(self, admin_client, make_book):
        make_book("A Wizard of Earthsea", status="published")
        r = admin_client.get("/admin/library/book/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "A Wizard of Earthsea" in body

    def test_search_filters_rows(self, admin_client, author, make_book):
        make_book("A Wizard of Earthsea", author=author, status="published")
        make_book("The Dispossessed", author=author, status="published")
        r = admin_client.get("/admin/library/book/?q=Wizard")
        body = r.content.decode()
        assert "A Wizard of Earthsea" in body
        assert "The Dispossessed" not in body

    def test_sort_by_column_returns_200(self, admin_client, author, make_book):
        make_book("Z book", author=author)
        make_book("A book", author=author)
        r = admin_client.get("/admin/library/book/?o=1")
        assert r.status_code == 200

    def test_list_filter_applies_status(self, admin_client, make_book):
        make_book("Drafted", status="draft")
        make_book("Published", status="published")
        r = admin_client.get("/admin/library/book/?status__exact=published")
        body = r.content.decode()
        assert "Published" in body
        assert "Drafted" not in body

    @pytest.mark.parametrize(
        "path",
        [
            "/admin/library/nonexistent/",
            "/admin/nonexistent_app/foo/",
            "/admin/auth/permission/",
        ],
        ids=("unknown_model", "unknown_app", "unregistered_model"),
    )
    def test_changelist_unknown_target_returns_404(self, admin_client, path):
        assert admin_client.get(path).status_code == 404

    def test_renders_none_value_as_dash(self, admin_client, make_author):
        make_author("Anon", email="a@a.com", born_in=None)
        r = admin_client.get("/admin/library/author/")
        assert r.status_code == 200
        assert "&mdash;" in r.content.decode()

    def test_changelist_title_is_capitalized(self, admin_client):
        r = admin_client.get("/admin/library/tag/")
        body = r.content.decode()
        assert ">Tags<" in body

    def test_changelist_without_filters_omits_sidebar(self, admin_client):
        r = admin_client.get("/admin/library/tag/")
        body = r.content.decode()
        assert "Filters" not in body
        assert "md:w-60" not in body

    def test_pagination_links_render_on_overflow(self, admin_client, make_tag):
        for i in range(120):
            make_tag(f"t{i:03d}")
        r = admin_client.get("/admin/library/tag/")
        body = r.content.decode()
        assert "page 1 of" in body
        assert "?p=2" in body


class TestChangelistChrome:
    def test_action_checkbox_column_is_hidden(self, admin_client):
        """`action_checkbox` is Django admin's synthetic column for selection.

        Selection is rendered through `selectable=`, so the literal header is gone.
        """
        r = admin_client.get("/admin/library/tag/")
        body = r.content.decode()
        assert "ACTION CHECKBOX" not in body
        assert "action checkbox" not in body.lower()

    def test_action_descriptions_are_interpolated(self, admin_client):
        """`mark_as_published` carries a `%(verbose_name_plural)s` slot."""
        body = admin_client.get("/admin/library/book/").content.decode()
        assert "%(verbose_name_plural)s" not in body
        assert "Mark selected books as published" in body

    def test_the_site_offers_no_delete_selected(self, admin_client):
        body = admin_client.get("/admin/library/tag/").content.decode()
        assert 'value="delete_selected"' not in body

    def test_action_labels_come_from_a_version_stable_admin_api(self, admin_client):
        """The changelist render raises no deprecation warning on either Django."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            warnings.simplefilter("error", PendingDeprecationWarning)
            r = admin_client.get("/admin/library/book/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "Mark selected books as published" in body


class TestBulkAction:
    @pytest.mark.parametrize(
        "origin",
        ["/admin/library/book/", "/admin/library/book/?status__exact=draft"],
        ids=["bare_changelist", "filtered_changelist"],
    )
    def test_an_empty_bulk_action_returns_to_the_changelist_it_came_from(
        self, admin_client, demo_data, origin
    ):
        r = admin_client.post_action("admin:bulk_action", {"action": ""}, origin=origin)
        assert r.status_code == 302
        assert r["Location"] == origin

    def test_filtered_changelist_form_carries_the_filter_in_its_origin(
        self, admin_client, demo_data
    ):
        r = admin_client.get("/admin/library/book/?status__exact=draft")
        rendered = form_fields(
            action_form(admin_client, r.content.decode(), "admin:bulk_action")
        )
        assert rendered["_next_form_origin"] == (
            "/admin/library/book/?status__exact=draft"
        )


class TestAddView:
    def test_add_get_renders_form(self, admin_client):
        r = admin_client.get("/admin/library/tag/add/")
        assert r.status_code == 200
        body = r.content.decode()
        assert 'name="name"' in body
        assert 'name="slug"' in body

    def test_add_post_creates_record(self, admin_client):
        r = admin_client.post_action(
            "admin:add",
            {"name": "SciFi", "slug": "scifi"},
            origin="/admin/library/tag/add/",
        )
        assert r.status_code == 302
        assert r["Location"] == "/admin/library/tag/"
        assert Tag.objects.filter(slug="scifi").exists()

    def test_add_post_invalid_rerenders_with_errors(self, admin_client):
        get = admin_client.get("/admin/library/tag/add/")
        form = action_form(admin_client, get.content.decode(), "admin:add")
        payload = {**hidden_fields(form), "name": "", "slug": ""}
        r = admin_client.post_action("admin:add", payload)
        assert r.status_code == 200
        body = r.content.decode()
        assert "This field is required" in body
        assert not Tag.objects.filter(name="").exists()


@dataclass(frozen=True, slots=True)
class WidgetAttributeCase:
    field: str
    present: tuple[str, ...]
    absent: tuple[str, ...] = ()


WIDGET_ATTRIBUTE_CASES = (
    pytest.param(
        WidgetAttributeCase(field="price", present=('step="0.01"',)),
        id="decimal_field_keeps_its_computed_step",
    ),
    pytest.param(
        WidgetAttributeCase(field="title", present=('maxlength="200"',)),
        id="char_field_keeps_its_maxlength",
    ),
    pytest.param(
        WidgetAttributeCase(
            field="title",
            present=("rounded-md border border-input",),
            absent=("vTextField",),
        ),
        id="shadcn_class_replaces_the_django_one",
    ),
)


class TestWidgetAttributes:
    @pytest.mark.parametrize("case", WIDGET_ATTRIBUTE_CASES)
    def test_add_form_input_carries_the_expected_attributes(self, admin_client, case):
        r = admin_client.get("/admin/library/book/add/")
        assert r.status_code == 200
        field = re.search(rf'<input[^>]*name="{case.field}"[^>]*>', r.content.decode())
        assert field is not None
        for fragment in case.present:
            assert fragment in field.group(0)
        for fragment in case.absent:
            assert fragment not in field.group(0)

    def test_a_decimal_price_saves_through_the_add_action(self, admin_client, author):
        r = admin_client.post_action(
            "admin:add",
            {
                "title": "Priced",
                "author": str(author.pk),
                "status": "draft",
                "price": "12.50",
                "chapters-TOTAL_FORMS": "0",
                "chapters-INITIAL_FORMS": "0",
                "chapters-MIN_NUM_FORMS": "0",
                "chapters-MAX_NUM_FORMS": "1000",
            },
            origin="/admin/library/book/add/",
        )
        assert r.status_code == 302
        assert str(Book.objects.get(title="Priced").price) == "12.50"


class TestInlineFieldIds:
    def test_every_rendered_id_on_a_change_page_is_unique(
        self, admin_client, book_with_two_chapters
    ):
        book, *_ = book_with_two_chapters
        r = admin_client.get(f"/admin/library/book/{book.pk}/change/")
        ids = re.findall(r'\sid="([^"]+)"', r.content.decode())
        assert len(ids) == len(set(ids))

    def test_a_keyed_row_namespaces_its_ids_by_primary_key(
        self, admin_client, book_with_one_chapter
    ):
        book, chapter = book_with_one_chapter
        r = admin_client.get(f"/admin/library/book/{book.pk}/change/")
        body = r.content.decode()
        assert f'id="id_chapter_{chapter.pk}_title"' in body
        assert 'id="id_chapter_add_title"' in body

    def test_a_layered_editor_shares_no_id_with_the_page_behind_it(
        self, admin_client, book_with_one_chapter
    ):
        """The chapter editor layered over the book editor repeats no `id_title`."""
        book, _chapter = book_with_one_chapter
        page = admin_client.get(f"/admin/library/book/{book.pk}/change/")
        new_chapter = Chapter.objects.create(
            book=book, number=2, title="Rising", word_count=200
        )
        layer = admin_client.get_zones(
            f"/admin/library/chapter/{new_chapter.pk}/change/", "record"
        )
        page_ids = element_ids(page.content.decode())
        layer_ids = element_ids(envelope_of(layer).html_for_zone("record"))

        assert "id_book_title" in page_ids
        assert "id_chapter_title" in layer_ids
        assert set(page_ids) & set(layer_ids) == set()
        assert len(page_ids + layer_ids) == len(set(page_ids + layer_ids))

    def test_every_field_label_points_at_its_own_input(
        self, admin_client, book_with_one_chapter
    ):
        book, chapter = book_with_one_chapter
        body = admin_client.get(
            f"/admin/library/book/{book.pk}/change/"
        ).content.decode()
        targets = re.findall(r'<label for="([^"]+)"', body)
        ids = element_ids(body)

        assert "id_book_title" in targets
        assert f"id_chapter_{chapter.pk}_title" in targets
        for target in targets:
            assert ids.count(target) == 1

    def test_the_wire_names_stay_unprefixed(self, admin_client, book_with_one_chapter):
        book, chapter = book_with_one_chapter
        r = admin_client.post_action(
            "admin:inline_change",
            {
                "_inline": "chapter",
                "_inline_pk": str(chapter.pk),
                "number": "1",
                "title": "Renamed",
                "word_count": "150",
            },
            origin=f"/admin/library/book/{book.pk}/change/",
        )
        assert r.status_code == 302
        chapter.refresh_from_db()
        assert chapter.title == "Renamed"


class TestChangeView:
    def test_change_get_renders_form(self, admin_client, make_tag):
        tag = make_tag("Old", slug="old")
        r = admin_client.get(f"/admin/library/tag/{tag.pk}/change/")
        assert r.status_code == 200
        body = r.content.decode()
        assert 'value="Old"' in body
        assert 'value="old"' in body

    def test_change_post_updates_record(self, admin_client, make_tag):
        tag = make_tag("Old")
        r = admin_client.post_action(
            "admin:change",
            {"name": "New", "slug": "new"},
            origin=f"/admin/library/tag/{tag.pk}/change/",
        )
        assert r.status_code == 302
        tag.refresh_from_db()
        assert tag.name == "New"
        assert tag.slug == "new"

    def test_change_get_unknown_pk_404(self, admin_client):
        r = admin_client.get("/admin/library/tag/99999/change/")
        assert r.status_code == 404


class TestDeleteView:
    def test_delete_get_renders_confirmation(self, admin_client, make_tag):
        tag = make_tag("Doomed")
        r = admin_client.get(f"/admin/library/tag/{tag.pk}/delete/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "Yes, delete" in body
        assert "Doomed" in body

    def test_delete_post_removes_record(self, admin_client, make_tag):
        tag = make_tag("Doomed")
        r = admin_client.post_action(
            "admin:delete", origin=f"/admin/library/tag/{tag.pk}/delete/"
        )
        assert r.status_code == 302
        assert not Tag.objects.filter(pk=tag.pk).exists()

    def test_delete_get_unknown_pk_404(self, admin_client):
        r = admin_client.get("/admin/library/tag/99999/delete/")
        assert r.status_code == 404

    def test_delete_post_unknown_pk_404(self, admin_client):
        r = admin_client.post_action(
            "admin:delete", origin="/admin/library/tag/99999/delete/"
        )
        assert r.status_code == 404


class TestInlines:
    def test_change_book_renders_keyed_chapter_rows(
        self, admin_client, book_with_two_chapters
    ):
        book, first, second = book_with_two_chapters
        r = admin_client.get(f"/admin/library/book/{book.pk}/change/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "chapters" in body.lower()
        assert f'data-next-key="{first.pk}"' in body
        assert f'data-next-key="{second.pk}"' in body
        assert 'value="Intro"' in body
        assert "Add chapter" in body

    def test_change_book_autocomplete_field_renders_select(self, admin_client, book):
        r = admin_client.get(f"/admin/library/book/{book.pk}/change/")
        assert r.status_code == 200
        body = r.content.decode()
        assert 'name="author"' in body
        assert '<select name="author"' in body

    def test_add_book_through_browser_flow(self, admin_client, author, make_tag):
        """Copy every rendered input from the GET to the POST like a browser."""
        tag = make_tag("Fantasy")

        get = admin_client.get("/admin/library/book/add/")
        assert get.status_code == 200
        rendered = form_fields(
            action_form(admin_client, get.content.decode(), "admin:add")
        )

        assert "csrfmiddlewaretoken" in rendered
        assert rendered.get("_next_form_origin") == "/admin/library/book/add/"

        payload = {
            **rendered,
            "title": "Browser-flow book",
            "author": str(author.pk),
            "tags": [str(tag.pk)],
            "status": "draft",
            "summary": "",
            "price": "0",
        }
        r = admin_client.post_action("admin:add", payload)
        assert r.status_code == 302, r.content.decode()[:1500]
        assert Book.objects.filter(title="Browser-flow book").exists()

    def test_change_book_main_save_ignores_absent_inlines(
        self, admin_client, book, make_chapter
    ):
        make_chapter(book)
        r = admin_client.post_action(
            "admin:change",
            {
                "title": "Renamed",
                "author": str(book.author.pk),
                "status": "draft",
                "summary": "",
                "price": "0",
            },
            origin=f"/admin/library/book/{book.pk}/change/",
        )
        assert r.status_code == 302
        book.refresh_from_db()
        assert book.title == "Renamed"
        assert Chapter.objects.filter(book=book).count() == 1


_INLINE_ACTIONS = ("admin:inline_change", "admin:inline_add")


@pytest.fixture()
def chapter_unique_constraint(monkeypatch):
    """Restate the chapter pair rule as a `UniqueConstraint`, checked separately.

    It runs through a different `_post_clean` check than `unique_together`.
    """
    monkeypatch.setattr(Chapter._meta, "unique_together", ())
    monkeypatch.setattr(
        Chapter._meta,
        "constraints",
        [UniqueConstraint(fields=["book", "number"], name="uniq_chapter_number")],
    )


class TestLiveInlines:
    """Each existing related row is its own keyed `admin:inline_change` form."""

    @staticmethod
    def _payload(action: str, pk: object, **fields: str) -> dict[str, str]:
        base = {"_inline": "chapter", "number": "9", "title": "X", "word_count": "10"}
        if action == "admin:inline_change":
            base["_inline_pk"] = str(pk)
        return {**base, **fields}

    def test_inline_change_saves_the_addressed_row(
        self, admin_client, book_with_two_chapters
    ):
        book, first, second = book_with_two_chapters
        r = admin_client.post_action(
            "admin:inline_change",
            {
                "_inline": "chapter",
                "_inline_pk": str(second.pk),
                "number": "2",
                "title": "Rising action",
                "word_count": "250",
            },
            origin=f"/admin/library/book/{book.pk}/change/",
        )
        assert r.status_code == 302
        assert r["Location"] == f"/admin/library/book/{book.pk}/change/"
        first.refresh_from_db()
        second.refresh_from_db()
        assert second.title == "Rising action"
        assert second.word_count == 250
        assert first.title == "Intro"

    def test_inline_add_creates_a_row(self, admin_client, book_with_two_chapters):
        book, *_ = book_with_two_chapters
        r = admin_client.post_action(
            "admin:inline_add",
            {
                "_inline": "chapter",
                "number": "3",
                "title": "Climax",
                "word_count": "300",
            },
            origin=f"/admin/library/book/{book.pk}/change/",
        )
        assert r.status_code == 302
        assert r["Location"] == f"/admin/library/book/{book.pk}/change/"
        assert Chapter.objects.filter(book=book, number=3, title="Climax").exists()

    @pytest.mark.parametrize("action", _INLINE_ACTIONS)
    def test_invalid_submit_rerenders_and_persists_nothing(
        self, admin_client, action, book_with_two_chapters
    ):
        book, first, _ = book_with_two_chapters
        before = Chapter.objects.filter(book=book).count()
        payload = self._payload(action, first.pk, title="")
        r = admin_client.post_action(
            action, payload, origin=f"/admin/library/book/{book.pk}/change/"
        )
        assert r.status_code == 200
        assert "This field is required" in r.content.decode()
        assert Chapter.objects.filter(book=book).count() == before
        first.refresh_from_db()
        assert first.title == "Intro"

    def test_invalid_partial_change_morphs_the_keyed_row_form(
        self, admin_client, book_with_two_chapters
    ):
        book, _first, second = book_with_two_chapters
        response = admin_client.post_action(
            "admin:inline_change",
            {
                "_inline": "chapter",
                "_inline_pk": str(second.pk),
                "number": "2",
                "title": "",
                "word_count": "250",
            },
            origin=f"/admin/library/book/{book.pk}/change/",
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
        assert meta["errors"]["title"] == ["This field is required."]
        html = envelope.ops[0]["html"]
        assert f'data-next-key="{second.pk}"' in html
        second.refresh_from_db()
        assert second.title == "Rising"

    def test_a_duplicate_number_comes_back_as_a_row_error(
        self, admin_client, book_with_two_chapters
    ):
        """`Chapter.unique_together` is checked before the INSERT reaches SQLite."""
        book, first, second = book_with_two_chapters
        r = admin_client.post_action(
            "admin:inline_change",
            {
                "_inline": "chapter",
                "_inline_pk": str(second.pk),
                "number": str(first.number),
                "title": "Dupe",
                "word_count": "10",
            },
            origin=f"/admin/library/book/{book.pk}/change/",
        )
        assert r.status_code == 200
        assert "Chapter with this Book and Number already exists." in r.content.decode()
        second.refresh_from_db()
        assert (second.number, second.title) == (2, "Rising")

    def test_a_duplicate_number_blocks_the_add_row(
        self, admin_client, book_with_two_chapters
    ):
        book, first, _second = book_with_two_chapters
        r = admin_client.post_action(
            "admin:inline_add",
            {
                "_inline": "chapter",
                "number": str(first.number),
                "title": "Dupe",
                "word_count": "10",
            },
            origin=f"/admin/library/book/{book.pk}/change/",
        )
        assert r.status_code == 200
        assert "Chapter with this Book and Number already exists." in r.content.decode()
        assert Chapter.objects.filter(book=book).count() == 2

    def test_a_duplicate_number_under_a_unique_constraint_is_a_row_error(
        self, admin_client, book_with_two_chapters, chapter_unique_constraint
    ):
        book, first, second = book_with_two_chapters
        r = admin_client.post_action(
            "admin:inline_change",
            {
                "_inline": "chapter",
                "_inline_pk": str(second.pk),
                "number": str(first.number),
                "title": "Dupe",
                "word_count": "10",
            },
            origin=f"/admin/library/book/{book.pk}/change/",
        )
        assert r.status_code == 200
        assert "Chapter with this Book and Number already exists." in r.content.decode()
        second.refresh_from_db()
        assert (second.number, second.title) == (2, "Rising")

    def test_a_unique_constraint_still_lets_a_free_number_through(
        self, admin_client, book_with_two_chapters, chapter_unique_constraint
    ):
        book, *_ = book_with_two_chapters
        r = admin_client.post_action(
            "admin:inline_add",
            {
                "_inline": "chapter",
                "number": "3",
                "title": "Climax",
                "word_count": "10",
            },
            origin=f"/admin/library/book/{book.pk}/change/",
        )
        assert r.status_code == 302
        assert Chapter.objects.filter(book=book, number=3).exists()

    def test_a_duplicate_number_leaves_a_sibling_book_alone(
        self, admin_client, book_with_two_chapters, make_book, make_chapter
    ):
        """The pair check is scoped to the parent, not to the number alone."""
        book, first, _second = book_with_two_chapters
        other = make_book("Other")
        make_chapter(other, first.number, "Elsewhere", 10)
        r = admin_client.post_action(
            "admin:inline_add",
            {
                "_inline": "chapter",
                "number": "3",
                "title": "Climax",
                "word_count": "10",
            },
            origin=f"/admin/library/book/{book.pk}/change/",
        )
        assert r.status_code == 302
        assert Chapter.objects.filter(book=book, number=3).exists()

    def test_a_duplicate_number_morphs_the_row_with_its_error(
        self, admin_client, book_with_two_chapters
    ):
        book, first, second = book_with_two_chapters
        response = admin_client.post_action(
            "admin:inline_change",
            {
                "_inline": "chapter",
                "_inline_pk": str(second.pk),
                "number": str(first.number),
                "title": "Dupe",
                "word_count": "10",
            },
            origin=f"/admin/library/book/{book.pk}/change/",
            partial=True,
        )
        assert response.status_code == 200
        assert response["X-Next-Form"] == "invalid"
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        html = envelope.ops[0]["html"]
        assert f'data-next-key="{second.pk}"' in html
        assert "Chapter with this Book and Number already exists." in html
        second.refresh_from_db()
        assert second.number == 2

    @pytest.mark.parametrize("action", _INLINE_ACTIONS)
    def test_unknown_inline_token_404(
        self, admin_client, action, book_with_two_chapters
    ):
        book, first, _ = book_with_two_chapters
        payload = self._payload(action, first.pk, _inline="nope")
        r = admin_client.post_action(
            action, payload, origin=f"/admin/library/book/{book.pk}/change/"
        )
        assert r.status_code == 404

    def test_inline_change_unknown_pk_404(self, admin_client, book_with_two_chapters):
        book, *_ = book_with_two_chapters
        payload = self._payload("admin:inline_change", "99999")
        r = admin_client.post_action(
            "admin:inline_change",
            payload,
            origin=f"/admin/library/book/{book.pk}/change/",
        )
        assert r.status_code == 404

    @pytest.mark.parametrize("action", _INLINE_ACTIONS)
    def test_anonymous_submit_redirects_to_login(
        self, next_client, action, book_with_two_chapters
    ):
        book, first, _ = book_with_two_chapters
        payload = self._payload(action, first.pk, title="Sneaky")
        r = next_client.post_action(
            action, payload, origin=f"/admin/library/book/{book.pk}/change/"
        )
        assert r.status_code == 302
        assert r["Location"].startswith("/admin/login/")
        first.refresh_from_db()
        assert first.title == "Intro"
        assert not Chapter.objects.filter(title="Sneaky").exists()

    @pytest.mark.parametrize("action", _INLINE_ACTIONS)
    def test_non_staff_submit_is_forbidden(
        self, next_client, django_user_model, action, book_with_two_chapters
    ):
        next_client.force_login(django_user_model.objects.create_user("intruder"))
        book, first, _ = book_with_two_chapters
        payload = self._payload(action, first.pk, title="Sneaky")
        r = next_client.post_action(
            action, payload, origin=f"/admin/library/book/{book.pk}/change/"
        )
        assert r.status_code == 403
        first.refresh_from_db()
        assert first.title == "Intro"
        assert not Chapter.objects.filter(title="Sneaky").exists()


class TestInlinePartialPatches:
    """Inline saves author replace, inner, and server-opened layer patches."""

    def test_partial_inline_change_replaces_row_and_inners_count(
        self, admin_client, book_with_one_chapter
    ):
        book, chapter = book_with_one_chapter
        response = admin_client.post_action(
            "admin:inline_change",
            {
                "_inline": "chapter",
                "_inline_pk": str(chapter.pk),
                "number": "1",
                "title": "Introduction",
                "word_count": "120",
            },
            origin=f"/admin/library/book/{book.pk}/change/",
            partial=True,
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["replace", "inner"]
        assert envelope.targets() == [
            {"css": f'form[data-next-key="{chapter.pk}"]'},
            {"css": '[data-inline-count="chapter"]'},
        ]
        assert f'data-next-key="{chapter.pk}"' in envelope.ops[0]["html"]
        assert 'value="Introduction"' in envelope.ops[0]["html"]
        assert "1 saved" in envelope.ops[1]["html"]
        chapter.refresh_from_db()
        assert chapter.title == "Introduction"
        assert chapter.word_count == 120

    def test_partial_inline_add_opens_layer_and_inners_count(
        self, admin_client, book_with_one_chapter
    ):
        book, _chapter = book_with_one_chapter
        response = admin_client.post_action(
            "admin:inline_add",
            {
                "_inline": "chapter",
                "number": "2",
                "title": "Rising",
                "word_count": "200",
            },
            origin=f"/admin/library/book/{book.pk}/change/",
            partial=True,
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["layer.open", "inner"]
        new_chapter = Chapter.objects.get(book=book, number=2, title="Rising")
        assert (
            envelope.ops[0]["href"]
            == f"/admin/library/chapter/{new_chapter.pk}/change/"
        )
        assert envelope.ops[0]["zone"] == "record"
        assert envelope.targets()[1] == {"css": '[data-inline-count="chapter"]'}
        assert "2 saved" in envelope.ops[1]["html"]


class TestLayerDismiss:
    """The chapter editor layer offers a server-side discard dismissal."""

    def test_layer_zone_render_offers_discard(
        self, admin_client, book_with_one_chapter
    ):
        _book, chapter = book_with_one_chapter
        response = admin_client.get_zones(
            f"/admin/library/chapter/{chapter.pk}/change/", "record"
        )
        assert response.status_code == 200
        html = envelope_of(response).html_for_zone("record")
        discard_url = admin_client.get_action_url("admin:discard")
        assert f'action="{discard_url}" method="post" data-next-action=' in html
        assert "Discard" in html

    def test_only_a_change_view_renders_the_discard_form(self, admin_client, chapter):
        """Discard dismisses an open editor, so the add view never offers it."""
        discard_url = admin_client.get_action_url("admin:discard")
        change = admin_client.get(
            f"/admin/library/chapter/{chapter.pk}/change/"
        ).content.decode()
        assert form_action(find_form(change, contains="Discard")) == discard_url

        add = admin_client.get("/admin/library/chapter/add/").content.decode()
        with pytest.raises(LookupError):
            find_form(add, action=discard_url)

    def test_discard_closes_layer_with_dismiss_reason(self, admin_client):
        response = admin_client.post_action("admin:discard", {}, partial=True)
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["layer.close"]
        op = envelope.ops[0]
        assert op["dismiss"] is True
        assert op["reason"] == "discarded"

    def test_discard_without_runtime_redirects_to_dashboard(self, admin_client):
        response = admin_client.post_action("admin:discard", {})
        assert response.status_code == 302
        assert response.headers["Location"] == "/admin/"


class TestHistoryView:
    def test_history_renders_after_change(self, admin_client, make_tag):
        tag = make_tag("Old")
        admin_client.post_action(
            "admin:change",
            {"name": "New", "slug": "new"},
            origin=f"/admin/library/tag/{tag.pk}/change/",
        )
        r = admin_client.get(f"/admin/library/tag/{tag.pk}/history/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "Changed" in body
        assert "admin" in body

    def test_history_unknown_pk_404(self, admin_client):
        r = admin_client.get("/admin/library/tag/99999/history/")
        assert r.status_code == 404


class TestInlineValidationFailure:
    """The add view's batch inline formset re-renders with errors on a bad row."""

    def test_inline_invalid_rerenders_with_errors(self, admin_client, author):
        get = admin_client.get("/admin/library/book/add/")
        rendered = form_fields(
            action_form(admin_client, get.content.decode(), "admin:add")
        )
        payload = {
            **rendered,
            "title": "Book with bad chapter",
            "author": str(author.pk),
            "status": "draft",
            "summary": "",
            "price": "0",
            "chapters-0-number": "1",
            "chapters-0-title": "",
            "chapters-0-word_count": "10",
        }
        r = admin_client.post_action("admin:add", payload)
        assert r.status_code == 200
        body = r.content.decode()
        assert "Book with bad chapter" in body
        assert not Book.objects.filter(title="Book with bad chapter").exists()


class TestSaveContinue:
    """`_save_continue` and `_save_addanother` route to change and add, not the list."""

    def test_save_continue_redirects_to_change(self, admin_client):
        r = admin_client.post_action(
            "admin:add",
            {"name": "Drama", "slug": "drama", "_save_continue": "1"},
            origin="/admin/library/tag/add/",
        )
        assert r.status_code == 302
        tag = Tag.objects.get(slug="drama")
        assert r["Location"] == f"/admin/library/tag/{tag.pk}/change/"

    def test_save_addanother_redirects_to_add(self, admin_client):
        r = admin_client.post_action(
            "admin:add",
            {"name": "Comedy", "slug": "comedy", "_save_addanother": "1"},
            origin="/admin/library/tag/add/",
        )
        assert r.status_code == 302
        assert r["Location"] == "/admin/library/tag/add/"
        assert Tag.objects.filter(slug="comedy").exists()

    def test_save_continue_on_change_keeps_pk(self, admin_client, make_tag):
        tag = make_tag("Old")
        r = admin_client.post_action(
            "admin:change",
            {"name": "Renamed", "slug": "renamed", "_save_continue": "1"},
            origin=f"/admin/library/tag/{tag.pk}/change/",
        )
        assert r.status_code == 302
        assert r["Location"] == f"/admin/library/tag/{tag.pk}/change/"
        tag.refresh_from_db()
        assert tag.name == "Renamed"


class TestCustomBulkAction:
    """`mark_as_published` sits on BookAdmin and answers through `admin:bulk_action`."""

    def test_action_present_in_changelist(self, admin_client):
        r = admin_client.get("/admin/library/book/")
        body = r.content.decode()
        assert 'value="mark_as_published"' in body
        assert "Mark selected books as published" in body

    def test_action_updates_status(self, admin_client, author, make_book):
        b1 = make_book("One", author=author, status=Book.DRAFT)
        b2 = make_book("Two", author=author, status=Book.DRAFT)
        r = admin_client.post_action(
            "admin:bulk_action",
            {
                "action": "mark_as_published",
                "_selected_action": [str(b1.pk), str(b2.pk)],
            },
            origin="/admin/library/book/",
        )
        assert r.status_code == 302
        b1.refresh_from_db()
        b2.refresh_from_db()
        assert b1.status == Book.PUBLISHED
        assert b2.status == Book.PUBLISHED


class TestActivityLog:
    """`action_dispatched` receiver writes one row per admin:* action."""

    def test_add_records_entry(self, admin_client):
        admin_client.post_action(
            "admin:add",
            {"name": "Thriller", "slug": "thriller"},
            origin="/admin/library/tag/add/",
        )
        entries = list(AdminActivityLog.objects.all())
        assert len(entries) == 1
        entry = entries[0]
        assert entry.action == "add"
        assert entry.app_label == "library"
        assert entry.model_name == "tag"
        assert entry.object_repr == "Thriller"
        assert entry.user is not None

    def test_bulk_action_records_entry_without_user(self, admin_client, book):
        admin_client.post_action(
            "admin:bulk_action",
            {"action": "mark_as_published", "_selected_action": [str(book.pk)]},
            origin="/admin/library/book/",
        )
        entries = list(AdminActivityLog.objects.filter(action="bulk_action"))
        assert len(entries) == 1
        assert entries[0].user is None

    def test_activity_page_renders_rows(self, admin_client):
        AdminActivityLog.objects.create(
            action="add",
            app_label="library",
            model_name="tag",
            object_repr="Sample tag",
            response_status=302,
        )
        r = admin_client.get("/admin/activity/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "Sample tag" in body
        assert "library.tag" in body


ROUTE_COLUMNS = ("url", "name", "file", "access", "intercept")


def route_table() -> list[dict[str, str]]:
    """Return every row `manage.py routes` prints, a routed row without `intercept`."""
    out = StringIO()
    call_command("routes", stdout=out)
    _header, *lines = out.getvalue().splitlines()
    return [
        dict(zip(ROUTE_COLUMNS, re.split(r"\s{2,}", line.strip()), strict=False))
        for line in lines
    ]


def route_rows() -> dict[str, dict[str, str]]:
    """Return the routed rows `manage.py routes` prints, keyed by URL."""
    return {row["url"]: row for row in route_table() if "intercept" not in row}


def intercept_rows() -> dict[str, dict[str, str]]:
    """Return the intercept rows `manage.py routes` prints, keyed by their zones."""
    return {row["intercept"]: row for row in route_table() if "intercept" in row}


class TestAppPageTrees:
    """Each app serves the pages of its own `pages/` tree under the one include."""

    HISTORY_URL = "/admin/<str:app_label>/<str:model_name>/<int:pk>/history/"
    HISTORY_NAME = "page_str_app_label_str_model_name_int_pk_history"

    def test_the_history_page_receives_the_prefix_kwargs(self, admin_client, make_book):
        book = make_book("Ulysses")
        r = admin_client.get(f"/admin/library/book/{book.pk}/history/")
        assert r.status_code == 200
        assert r.resolver_match.kwargs == {
            "app_label": "library",
            "model_name": "book",
            "pk": book.pk,
        }
        assert "Ulysses" in r.content.decode()

    def test_the_activity_url_and_name_are_unchanged(self):
        assert page_reverse("activity") == "/admin/activity/"
        assert reverse("next:page_activity") == "/admin/activity/"

    def test_the_history_url_and_name_are_unchanged(self):
        kwargs = {"app_label": "library", "model_name": "tag", "pk": 7}
        trail = "[str:app_label]/[str:model_name]/[int:pk]/history"
        expected = "/admin/library/tag/7/history/"
        assert page_reverse(trail, **kwargs) == expected
        assert reverse(f"next:{self.HISTORY_NAME}", kwargs=kwargs) == expected

    def test_the_route_map_names_the_file_of_each_page(self):
        rows = route_rows()
        assert rows["/admin/activity/"] == {
            "url": "/admin/activity/",
            "name": "next:page_activity",
            "file": "admin_audit/pages/activity/page.py",
            "access": ACTIVITY_ACCESS,
        }
        assert rows[self.HISTORY_URL] == {
            "url": self.HISTORY_URL,
            "name": f"next:{self.HISTORY_NAME}",
            "file": (
                "shadcn_admin/pages/(app)/[str:app_label]/[str:model_name]/[int:pk]/"
                "history/page.py"
            ),
            "access": "login, perm shadcn_admin.use_admin",
        }

    def test_a_tree_rule_routes_no_page_of_its_own(self, admin_client):
        rows = route_rows()
        files = {row["file"] for row in rows.values()}
        assert {"admin_audit/pages/page.py", "library/pages/page.py"}.isdisjoint(files)
        assert rows["/admin/"]["file"] == "shadcn_admin/pages/(app)/page.py"
        assert admin_client.get("/admin/").status_code == 200

    def test_the_admin_tree_holds_the_auth_pages(self):
        rows = route_rows()
        admin_rows = [
            row for row in rows.values() if row["file"].startswith("shadcn_admin/")
        ]
        assert len(admin_rows) == 8
        assert rows["/admin/"]["name"] == "next:page_"
        assert (
            rows["/admin/login/"]["file"] == "shadcn_admin/pages/(auth)/login/page.py"
        )
        assert rows["/admin/logout/"]["name"] == "next:page_logout"

    @pytest.mark.parametrize(
        "path", ["/admin/activity/", "/admin/library/book/{pk}/history/"]
    )
    def test_an_app_page_keeps_the_admin_shell(self, admin_client, book, path):
        body = admin_client.get(path.format(pk=book.pk)).content.decode()
        logout_url = admin_client.get_action_url("admin:logout")
        assert body.count("<!DOCTYPE html>") == 1
        assert body.count("<aside") == 1
        assert body.count(f'action="{logout_url}"') == 1
        assert body.count(">Library<") == 1

    @pytest.mark.parametrize("path", ["/admin/login/", "/admin/logout/"])
    def test_the_auth_pages_keep_the_centered_chrome(self, next_client, path):
        body = next_client.get(path).content.decode()
        assert body.count("<!DOCTYPE html>") == 1
        assert "<aside" not in body
        assert "<header" not in body
        assert "Breadcrumb" not in body
        assert "Sign out" not in body
        assert body.count("<main") == 1
        assert (
            'class="flex min-h-screen items-center justify-center px-4 py-12"' in body
        )

    def test_the_dashboard_keeps_the_sidebar_shell(self, admin_client):
        body = admin_client.get("/admin/").content.decode()
        assert body.count("<aside") == 1
        assert body.count("<main") == 1

    def test_no_source_of_the_example_reads_the_removed_auth_flag(self):
        flag = "is_auth_page"
        sources = [
            path
            for pattern in ("*.djx", "*.py")
            for root in (EXAMPLE_DIR / "chrome", EXAMPLE_DIR / "shadcn_admin")
            for path in root.rglob(pattern)
        ]
        assert sources
        assert [p for p in sources if flag in p.read_text()] == []

    def test_the_login_page_links_the_auth_stylesheet_from_the_group(self, next_client):
        body = next_client.get("/admin/login/").content.decode()
        (href,) = re.findall(r'href="([^"]*\(auth\)/layout\.css)[^"]*"', body)
        assert href == STATIC_URL + "next/shadcn_admin/pages/(auth)/layout.css"
        assert finders.find(href.removeprefix(STATIC_URL)).endswith("(auth)/layout.css")

    def test_the_dashboard_does_not_load_the_auth_stylesheet(self, admin_client):
        assert "(auth)" not in admin_client.get("/admin/").content.decode()


class TestBreadcrumbs:
    """The shell renders the crumbs page metadata declares, tree by tree."""

    def test_the_dashboard_is_its_own_crumb(self, admin_client):
        body = admin_client.get("/admin/").content.decode()
        assert crumbs(body) == [("Dashboard", "/admin/", True)]

    def test_a_changelist_links_back_to_the_dashboard(self, admin_client):
        body = admin_client.get("/admin/library/book/").content.decode()
        assert crumbs(body) == [
            ("Dashboard", "/admin/", False),
            ("Books", "/admin/library/book/", True),
        ]

    def test_a_delete_page_reverses_its_ancestors_with_its_kwargs(
        self, admin_client, book
    ):
        body = admin_client.get(f"/admin/library/book/{book.pk}/delete/").content
        assert crumbs(body.decode()) == [
            ("Dashboard", "/admin/", False),
            ("Books", "/admin/library/book/", False),
        ]

    def test_the_history_crumb_reverses_with_the_prefix_kwargs(
        self, admin_client, book
    ):
        url = f"/admin/library/book/{book.pk}/history/"
        body = admin_client.get(url).content.decode()
        assert crumbs(body) == [
            ("Dashboard", "/admin/", False),
            ("Books", "/admin/library/book/", False),
            ("History", url, True),
        ]

    def test_the_activity_crumb_follows_the_dashboard(self, admin_client):
        body = admin_client.get("/admin/activity/").content.decode()
        assert crumbs(body) == [
            ("Dashboard", "/admin/", False),
            ("Activity", "/admin/activity/", True),
        ]

    def test_the_auth_chrome_renders_no_crumbs(self, next_client):
        body = next_client.get("/admin/login/").content.decode()
        assert crumbs(body) == []


class TestFlashMessages:
    """`django.contrib.messages` round-trips through the flash_messages component."""

    def test_add_flashes_success(self, admin_client):
        r = admin_client.post_action(
            "admin:add",
            {"name": "Mystery", "slug": "mystery"},
            origin="/admin/library/tag/add/",
            follow=True,
        )
        body = r.content.decode()
        assert "The tag Mystery was added successfully." in body

    def test_change_flashes_success(self, admin_client, make_tag):
        tag = make_tag("Old")
        r = admin_client.post_action(
            "admin:change",
            {"name": "New", "slug": "new"},
            origin=f"/admin/library/tag/{tag.pk}/change/",
            follow=True,
        )
        body = r.content.decode()
        assert "The tag New was updated successfully." in body

    def test_delete_flashes_success(self, admin_client, make_tag):
        tag = make_tag("Doomed")
        r = admin_client.post_action(
            "admin:delete", origin=f"/admin/library/tag/{tag.pk}/delete/", follow=True
        )
        body = r.content.decode()
        assert "The tag Doomed was deleted successfully." in body

    def test_bulk_action_flashes_message_user(self, admin_client, make_book):
        """Django's `ModelAdmin.message_user` writes via the messages framework."""
        book = make_book("X", status=Book.DRAFT)
        r = admin_client.post_action(
            "admin:bulk_action",
            {"action": "mark_as_published", "_selected_action": [str(book.pk)]},
            origin="/admin/library/book/",
            follow=True,
        )
        body = r.content.decode()
        assert "marked as published" in body.lower()

    def test_login_success_flashes_welcome(self, next_client, admin_user):
        r = next_client.post_action(
            "admin:login",
            {"username": "admin", "password": "admin-pass", "next": "/admin/"},
            follow=True,
        )
        body = r.content.decode()
        assert "Welcome, admin." in body


class TestPageMetadata:
    """Every page is `noindex, nofollow`, model pages are titled after the model."""

    def test_login_page_carries_the_site_default_and_noindex(self, next_client):
        response = next_client.get("/admin/login/")
        assert_metadata(response, title="next.dj admin", robots="noindex, nofollow")
        assert response["X-Robots-Tag"] == "noindex, nofollow"

    @pytest.mark.parametrize(
        "suffix",
        ["", "add/", "{pk}/change/", "{pk}/delete/", "{pk}/history/"],
        ids=["changelist", "add", "change", "delete", "history"],
    )
    def test_model_pages_are_titled_after_the_model(self, admin_client, book, suffix):
        path = "/admin/library/book/" + suffix.format(pk=book.pk)
        assert_metadata(
            admin_client.get(path),
            title="Books · next.dj admin",
            robots="noindex, nofollow",
        )

    @pytest.mark.parametrize(
        "path",
        ["/robots.txt", "/sitemap.xml", "/admin/robots.txt", "/admin/sitemap.xml"],
        ids=["root-robots", "root-sitemap", "admin-robots", "admin-sitemap"],
    )
    def test_no_seo_route_is_mounted(self, admin_client, path):
        assert admin_client.get(path).status_code == 404


def _book_payload(book: Book, **fields: str) -> dict[str, str]:
    return {
        "title": book.title,
        "author": str(book.author.pk),
        "status": book.status,
        "summary": "",
        "price": "0",
        **fields,
    }


class StockChapterInline(admin.TabularInline):
    model = Chapter


class TestObjectPermissions:
    """The editor changes and deletes draft books only, the superuser everything."""

    def test_has_perm_asks_the_predicates_for_the_editor(self, editor, make_book):
        draft = make_book(status=Book.DRAFT)
        published = make_book(status=Book.PUBLISHED)
        assert editor.has_perm("library.change_book", draft) is True
        assert editor.has_perm("library.change_book", published) is False
        assert editor.has_perm("library.change_book") is True
        assert editor.has_perm("library.delete_book", published) is False
        assert editor.has_perm("admin_audit.view_adminactivitylog") is False

    def test_the_superuser_holds_every_row(self, admin_user, make_book):
        published = make_book(status=Book.PUBLISHED)
        assert admin_user.has_perm("library.change_book", published) is True
        assert admin_user.has_perm("library.delete_book", published) is True

    def test_the_editor_sees_the_library_and_nothing_else(self, editor_client):
        body = editor_client.get("/admin/").content.decode()
        assert "Books" in body
        assert "Admin activity" not in body
        assert "Users" not in body

    def test_a_draft_offers_save_and_delete_to_the_editor(
        self, editor_client, make_book
    ):
        draft = make_book(status=Book.DRAFT)
        body = editor_client.get(f"/admin/library/book/{draft.pk}/change/")
        html = body.content.decode()
        assert 'name="_save_continue"' in html
        assert f"/admin/library/book/{draft.pk}/delete/" in html
        assert "data-read-only" not in html

    def test_a_published_book_is_read_only_for_the_editor(
        self, editor_client, make_book
    ):
        published = make_book(status=Book.PUBLISHED)
        html = editor_client.get(
            f"/admin/library/book/{published.pk}/change/"
        ).content.decode()
        assert "data-read-only" in html
        assert 'name="_save_continue"' not in html
        assert f"/admin/library/book/{published.pk}/delete/" not in html

    def test_a_published_book_keeps_its_buttons_for_the_superuser(
        self, admin_client, make_book
    ):
        published = make_book(status=Book.PUBLISHED)
        html = admin_client.get(
            f"/admin/library/book/{published.pk}/change/"
        ).content.decode()
        assert 'name="_save_continue"' in html
        assert "data-read-only" not in html

    def test_the_editor_saves_a_draft(self, editor_client, make_book):
        draft = make_book("Draft", status=Book.DRAFT)
        r = editor_client.post_action(
            "admin:change",
            _book_payload(draft, title="Revised"),
            origin=f"/admin/library/book/{draft.pk}/change/",
        )
        assert r.status_code == 302
        draft.refresh_from_db()
        assert draft.title == "Revised"

    def test_the_editor_cannot_save_a_published_book(self, editor_client, make_book):
        published = make_book("Locked", status=Book.PUBLISHED)
        r = editor_client.post_action(
            "admin:change",
            _book_payload(published, title="Hacked"),
            origin=f"/admin/library/book/{published.pk}/change/",
        )
        assert r.status_code == 403
        published.refresh_from_db()
        assert published.title == "Locked"

    def test_demoting_a_published_book_to_draft_is_refused(
        self, editor_client, make_book
    ):
        published = make_book("Locked", status=Book.PUBLISHED)
        r = editor_client.post_action(
            "admin:change",
            _book_payload(published, title="Hacked", status=Book.DRAFT),
            origin=f"/admin/library/book/{published.pk}/change/",
        )
        assert r.status_code == 403
        published.refresh_from_db()
        assert (published.title, published.status) == ("Locked", Book.PUBLISHED)

    def test_an_invalid_demotion_is_refused_too(self, editor_client, make_book):
        published = make_book("Locked", status=Book.PUBLISHED)
        r = editor_client.post_action(
            "admin:change",
            _book_payload(published, title="", status=Book.DRAFT),
            origin=f"/admin/library/book/{published.pk}/change/",
        )
        assert r.status_code == 403

    def test_publishing_a_draft_shows_the_refusal(self, editor_client, make_book):
        draft = make_book("Draft", status=Book.DRAFT)
        r = editor_client.post_action(
            "admin:change",
            _book_payload(draft, status=Book.PUBLISHED),
            origin=f"/admin/library/book/{draft.pk}/change/",
        )
        assert "You may not save this book in this state." in r.content.decode()

    def test_the_editor_may_not_publish_a_draft(self, editor_client, make_book):
        draft = make_book("Draft", status=Book.DRAFT)
        r = editor_client.post_action(
            "admin:change",
            _book_payload(draft, status=Book.PUBLISHED),
            origin=f"/admin/library/book/{draft.pk}/change/",
        )
        assert r.status_code == 200
        draft.refresh_from_db()
        assert draft.status == Book.DRAFT

    def test_the_editor_may_not_add_a_published_book(self, editor_client, author):
        r = editor_client.post_action(
            "admin:add",
            {
                "title": "Born published",
                "author": str(author.pk),
                "status": Book.PUBLISHED,
                "summary": "",
                "price": "0",
            },
            origin="/admin/library/book/add/",
        )
        assert r.status_code == 200
        assert not Book.objects.filter(title="Born published").exists()

    def test_the_superuser_publishes_a_draft(self, admin_client, make_book):
        draft = make_book("Draft", status=Book.DRAFT)
        r = admin_client.post_action(
            "admin:change",
            _book_payload(draft, status=Book.PUBLISHED),
            origin=f"/admin/library/book/{draft.pk}/change/",
        )
        assert r.status_code == 302
        draft.refresh_from_db()
        assert draft.status == Book.PUBLISHED

    def test_the_superuser_saves_a_published_book(self, admin_client, make_book):
        published = make_book("Locked", status=Book.PUBLISHED)
        r = admin_client.post_action(
            "admin:change",
            _book_payload(published, title="Revised"),
            origin=f"/admin/library/book/{published.pk}/change/",
        )
        assert r.status_code == 302
        published.refresh_from_db()
        assert published.title == "Revised"

    def test_the_editor_cannot_delete_a_published_book(self, editor_client, make_book):
        published = make_book(status=Book.PUBLISHED)
        r = editor_client.post_action(
            "admin:delete", origin=f"/admin/library/book/{published.pk}/delete/"
        )
        assert r.status_code == 403
        assert Book.objects.filter(pk=published.pk).exists()

    def test_the_editor_deletes_a_draft(self, editor_client, make_book):
        draft = make_book(status=Book.DRAFT)
        r = editor_client.post_action(
            "admin:delete", origin=f"/admin/library/book/{draft.pk}/delete/"
        )
        assert r.status_code == 302
        assert not Book.objects.filter(pk=draft.pk).exists()

    def test_chapters_of_a_published_book_are_locked_for_the_editor(
        self, editor_client, make_book, make_chapter
    ):
        published = make_book(status=Book.PUBLISHED)
        chapter = make_chapter(published)
        r = editor_client.post_action(
            "admin:inline_change",
            {
                "_inline": "chapter",
                "_inline_pk": str(chapter.pk),
                "number": "1",
                "title": "Hacked",
                "word_count": "1",
            },
            origin=f"/admin/library/book/{published.pk}/change/",
        )
        assert r.status_code == 403
        chapter.refresh_from_db()
        assert chapter.title == "Intro"

    def test_the_editor_cannot_add_a_chapter_to_a_published_book(
        self, editor_client, make_book
    ):
        published = make_book(status=Book.PUBLISHED)
        r = editor_client.post_action(
            "admin:inline_add",
            {"_inline": "chapter", "number": "9", "title": "New", "word_count": "1"},
            origin=f"/admin/library/book/{published.pk}/change/",
        )
        assert r.status_code == 403
        assert not published.chapters.exists()

    def test_a_standalone_chapter_of_a_published_book_is_locked(
        self, editor_client, make_book, make_chapter
    ):
        chapter = make_chapter(make_book(status=Book.PUBLISHED))
        r = editor_client.post_action(
            "admin:delete", origin=f"/admin/library/chapter/{chapter.pk}/delete/"
        )
        assert r.status_code == 403

    def test_bulk_publish_skips_the_rows_the_editor_may_not_change(
        self, editor_client, author, make_book
    ):
        draft = make_book("Draft", author=author, status=Book.DRAFT)
        archived = make_book("Archived", author=author, status=Book.ARCHIVED)
        editor_client.post_action(
            "admin:bulk_action",
            {
                "action": "mark_as_published",
                "_selected_action": [str(draft.pk), str(archived.pk)],
            },
            origin="/admin/library/book/",
        )
        draft.refresh_from_db()
        archived.refresh_from_db()
        assert draft.status == Book.DRAFT
        assert archived.status == Book.ARCHIVED

    @pytest.mark.parametrize("account", ["editor", "superuser"])
    def test_a_forged_delete_selected_deletes_nothing(
        self, account, editor_client, admin_client, make_book
    ):
        client = editor_client if account == "editor" else admin_client
        book = make_book(status=Book.DRAFT)
        client.post_action(
            "admin:bulk_action",
            {
                "action": "delete_selected",
                "post": "yes",
                "_selected_action": [str(book.pk)],
            },
            origin="/admin/library/book/",
        )
        assert Book.objects.filter(pk=book.pk).exists()

    def test_the_superuser_bulk_publishes_any_book(
        self, admin_client, author, make_book
    ):
        archived = make_book("Archived", author=author, status=Book.ARCHIVED)
        admin_client.post_action(
            "admin:bulk_action",
            {"action": "mark_as_published", "_selected_action": [str(archived.pk)]},
            origin="/admin/library/book/",
        )
        archived.refresh_from_db()
        assert archived.status == Book.PUBLISHED

    def test_deleting_an_author_never_cascades_past_the_editor(
        self, editor_client, make_book
    ):
        published = make_book(status=Book.PUBLISHED)
        author_pk = published.author_id
        page = editor_client.get(f"/admin/library/author/{author_pk}/delete/")
        assert page.status_code == 200
        assert "books" in page.content.decode().lower()
        r = editor_client.post_action(
            "admin:delete", origin=f"/admin/library/author/{author_pk}/delete/"
        )
        assert r.status_code == 403
        assert Book.objects.filter(pk=published.pk).exists()

    def test_the_editor_deletes_an_author_of_drafts_only(
        self, editor_client, make_book
    ):
        draft = make_book(status=Book.DRAFT)
        r = editor_client.post_action(
            "admin:delete", origin=f"/admin/library/author/{draft.author_id}/delete/"
        )
        assert r.status_code == 302
        assert not Book.objects.filter(pk=draft.pk).exists()

    def test_the_superuser_deletes_an_author_and_their_books(
        self, admin_client, make_book
    ):
        published = make_book(status=Book.PUBLISHED)
        r = admin_client.post_action(
            "admin:delete",
            origin=f"/admin/library/author/{published.author_id}/delete/",
        )
        assert r.status_code == 302
        assert not Book.objects.filter(pk=published.pk).exists()

    def test_a_chapter_add_offers_and_accepts_drafts_only(
        self, editor_client, make_book
    ):
        draft = make_book("Open draft", status=Book.DRAFT)
        published = make_book("Closed book", status=Book.PUBLISHED)
        html = editor_client.get("/admin/library/chapter/add/").content.decode()
        assert "Open draft" in html
        assert "Closed book" not in html
        r = editor_client.post_action(
            "admin:add",
            {
                "book": str(published.pk),
                "number": "9",
                "title": "In",
                "word_count": "1",
            },
            origin="/admin/library/chapter/add/",
        )
        assert r.status_code == 200
        assert not Chapter.objects.filter(book=published).exists()
        r = editor_client.post_action(
            "admin:add",
            {"book": str(draft.pk), "number": "9", "title": "In", "word_count": "1"},
            origin="/admin/library/chapter/add/",
        )
        assert r.status_code == 302
        assert Chapter.objects.filter(book=draft).exists()

    def test_a_chapter_cannot_move_into_a_published_book(
        self, editor_client, make_book, make_chapter
    ):
        chapter = make_chapter(make_book(status=Book.DRAFT))
        published = make_book(status=Book.PUBLISHED)
        editor_client.post_action(
            "admin:change",
            {"book": str(published.pk), "number": "7", "title": "M", "word_count": "1"},
            origin=f"/admin/library/chapter/{chapter.pk}/change/",
        )
        chapter.refresh_from_db()
        assert chapter.book.status == Book.DRAFT

    def test_the_superuser_adds_a_chapter_to_a_published_book(
        self, admin_client, make_book
    ):
        published = make_book(status=Book.PUBLISHED)
        r = admin_client.post_action(
            "admin:add",
            {
                "book": str(published.pk),
                "number": "9",
                "title": "In",
                "word_count": "1",
            },
            origin="/admin/library/chapter/add/",
        )
        assert r.status_code == 302

    @pytest.mark.parametrize(
        "path",
        [
            "/admin/auth/user/",
            "/admin/auth/user/{pk}/change/",
            "/admin/auth/user/{pk}/history/",
            "/admin/auth/user/{pk}/delete/",
            "/admin/auth/user/add/",
            "/admin/auth/group/",
        ],
    )
    def test_the_editor_reads_nothing_outside_the_library(
        self, path, editor_client, admin_client, admin_user
    ):
        url = path.format(pk=admin_user.pk)
        assert editor_client.get(url).status_code == 403
        assert admin_client.get(url).status_code == 200

    def test_a_tag_on_a_published_book_stays_for_the_editor(
        self, editor_client, make_book, make_tag
    ):
        tag = make_tag("Classic")
        make_book(status=Book.PUBLISHED).tags.add(tag)
        r = editor_client.post_action(
            "admin:delete", origin=f"/admin/library/tag/{tag.pk}/delete/"
        )
        assert r.status_code == 403
        assert Tag.objects.filter(pk=tag.pk).exists()

    def test_a_tag_on_drafts_only_is_the_editors_to_delete(
        self, editor_client, make_book, make_tag
    ):
        tag = make_tag("Working title")
        make_book(status=Book.DRAFT).tags.add(tag)
        r = editor_client.post_action(
            "admin:delete", origin=f"/admin/library/tag/{tag.pk}/delete/"
        )
        assert r.status_code == 302
        assert not Tag.objects.filter(pk=tag.pk).exists()

    def test_the_sidebar_offers_the_activity_feed_to_its_readers(
        self, editor_client, admin_client
    ):
        assert "Activity feed" not in editor_client.get("/admin/").content.decode()
        assert "Activity feed" in admin_client.get("/admin/").content.decode()

    def test_the_activity_feed_refuses_the_editor(self, editor_client, admin_client):
        assert editor_client.get("/admin/activity/").status_code == 403
        assert admin_client.get("/admin/activity/").status_code == 200

    def test_group_rights_on_a_plain_model_keep_save_and_delete(
        self, django_user_model
    ):
        role = Group.objects.create(name="Group managers")
        role.permissions.set(
            Permission.objects.filter(
                content_type__app_label="auth",
                codename__in=["view_group", "change_group", "delete_group"],
            )
        )
        manager = django_user_model.objects.create_user("manager", is_staff=True)
        manager.groups.add(role)
        target = Group.objects.create(name="Reviewers")
        client = NextClient()
        client.force_login(manager)

        html = client.get(f"/admin/auth/group/{target.pk}/change/").content.decode()
        assert 'name="_save_continue"' in html
        assert f"/admin/auth/group/{target.pk}/delete/" in html
        assert "data-read-only" not in html

        r = client.post_action(
            "admin:change",
            {"name": "Senior reviewers"},
            origin=f"/admin/auth/group/{target.pk}/change/",
        )
        assert r.status_code == 302
        target.refresh_from_db()
        assert target.name == "Senior reviewers"

    def test_a_plain_model_without_the_right_reads_only(self, django_user_model):
        role = Group.objects.create(name="Group readers")
        role.permissions.set(
            Permission.objects.filter(
                content_type__app_label="auth", codename="view_group"
            )
        )
        reader = django_user_model.objects.create_user("reader", is_staff=True)
        reader.groups.add(role)
        client = NextClient()
        client.force_login(reader)
        html = client.get(f"/admin/auth/group/{role.pk}/change/").content.decode()
        assert "data-read-only" in html
        assert f"/admin/auth/group/{role.pk}/delete/" not in html

    def test_the_editor_sees_the_chapters_of_a_published_book_read_only(
        self, editor_client, make_book, make_chapter
    ):
        published = make_book(status=Book.PUBLISHED)
        chapter = make_chapter(published)
        html = editor_client.get(
            f"/admin/library/book/{published.pk}/change/"
        ).content.decode()
        assert f'data-inline-read-only="{chapter.pk}"' in html
        assert f'data-next-key="{chapter.pk}"' not in html
        assert 'data-next-key="chapter"' not in html
        assert "Add chapter" not in html

    @pytest.mark.parametrize(
        ("account", "status"),
        [("editor", Book.DRAFT), ("superuser", Book.PUBLISHED)],
        ids=["editor-draft", "superuser-published"],
    )
    def test_inline_rows_stay_editable_where_the_right_holds(
        self, account, status, editor_client, admin_client, make_book, make_chapter
    ):
        client = editor_client if account == "editor" else admin_client
        book = make_book(status=status)
        chapter = make_chapter(book)
        html = client.get(f"/admin/library/book/{book.pk}/change/").content.decode()
        assert f'data-next-key="{chapter.pk}"' in html
        assert 'data-next-key="chapter"' in html
        assert "data-inline-read-only" not in html

    def test_a_stock_inline_asks_the_model_level_rights(self, rf, admin_user, book):
        request = rf.get("/")
        request.user = admin_user
        spec = AdminFormSpec.resolve(request, "library", "book", book.pk)
        rights = AdminInlineSpec(spec, StockChapterInline(Book, admin.site)).rights
        assert rights == InlineRights(
            "library.change_chapter", "library.add_chapter", book, row_rules=False
        )

    def test_seed_demo_creates_the_editors_once(self, db, django_user_model):
        call_command("seed_demo", stdout=StringIO())
        call_command("seed_demo", stdout=StringIO())
        for name in ("editor", "copyeditor"):
            editor = django_user_model.objects.get(username=name)
            assert editor.is_staff is True
            assert editor.is_superuser is False
            assert editor.check_password(name) is True

    def test_the_permission_checks_find_nothing(self, db):
        found = {message.id for message in run_checks()}
        assert found.isdisjoint(PERMISSION_CHECKS)


QUICK_EDIT = "quick-edit"
HISTORY_PEEK = "history-peek"
MANUSCRIPT_PEEK = "manuscript-peek"
CHANGELIST = "changelist"
INTERCEPT_CHECKS = frozenset(
    [f"next.E{number}" for number in range(169, 177)]
    + [f"next.W{number}" for number in range(136, 141)]
)
REQUIRED = "This field is required."


def change_url(book: Book) -> str:
    return f"/admin/library/book/{book.pk}/change/"


def history_url(book: Book) -> str:
    return f"/admin/library/book/{book.pk}/history/"


def meta_op(response) -> dict:
    (op,) = [op for op in envelope_of(response).ops if op["op"] == "meta"]
    return op


def quick_save(client: NextClient, book: Book, **fields: str):
    return client.post_action(
        "admin:change",
        _book_payload(book, **fields),
        origin=change_url(book),
        partial=True,
        zones=QUICK_EDIT,
    )


def log_changes(user, book: Book, count: int) -> None:
    content_type = ContentType.objects.get_for_model(Book)
    for number in range(1, count + 1):
        LogEntry.objects.create(
            user=user,
            content_type=content_type,
            object_id=str(book.pk),
            object_repr=str(book),
            action_flag=CHANGE,
            change_message=f"Revision {number}",
        )


class TestInterceptChecks:
    def test_the_intercept_checks_find_nothing(self, db):
        found = {message.id for message in run_checks()}
        assert found.isdisjoint(INTERCEPT_CHECKS)

    def test_the_route_map_lists_each_intercept_after_its_target(self):
        rows = intercept_rows()
        pages = "shadcn_admin/pages/(app)/[str:app_label]/[str:model_name]"
        admin_access = "login, perm shadcn_admin.use_admin"
        assert rows[QUICK_EDIT] == {
            "url": "/admin/<str:app_label>/<str:model_name>/<int:pk>/change/",
            "name": "-",
            "file": f"{pages}/(.)[int:pk]/change/page.py",
            "access": admin_access,
            "intercept": QUICK_EDIT,
        }
        assert rows[HISTORY_PEEK]["file"] == (
            f"{pages}/[int:pk]/change/(..)history/page.py"
        )
        assert rows[HISTORY_PEEK]["url"] == (
            "/admin/<str:app_label>/<str:model_name>/<int:pk>/history/"
        )
        assert rows[MANUSCRIPT_PEEK]["access"] == (
            f"{admin_access}, perm library.view_book on library.Book"
        )
        urls = [row["url"] for row in route_table()]
        target = urls.index(rows[QUICK_EDIT]["url"])
        assert urls[target + 1] == urls[target]


class TestQuickEdit:
    """`(.)[int:pk]/change/` serves the change form as a modal over the changelist."""

    def test_each_row_offers_the_quick_editor_beside_the_plain_link(
        self, admin_client, make_book
    ):
        book = make_book("Dracula")
        body = admin_client.get("/admin/library/book/").content.decode()
        quick = find_anchor(body, href=change_url(book), text="Quick edit")
        assert f'data-next-layer="{QUICK_EDIT}"' in quick
        assert f'data-next-accepted="{CHANGELIST}"' in quick
        plain = find_anchor(body, href=change_url(book), text="Dracula")
        assert "data-next-layer" not in plain
        assert f'data-next-key="{book.pk}"' in body

    def test_the_changelist_rows_render_as_their_own_zone(
        self, admin_client, make_book
    ):
        make_book("Dracula")
        r = admin_client.get_zones("/admin/library/book/?q=Drac", CHANGELIST)
        assert r.status_code == 200
        assert "Dracula" in envelope_of(r).html_for_zone(CHANGELIST)

    def test_a_zone_request_serves_the_quick_editor(self, admin_client, make_book):
        book = make_book("Dracula")
        r = admin_client.get_zones(change_url(book), QUICK_EDIT)
        assert r.status_code == 200
        assert envelope_of(r).op_verbs() == ["morph", "meta"]
        html = envelope_of(r).html_for_zone(QUICK_EDIT)
        form = action_form(admin_client, html, "admin:change")
        assert f'data-next-target="{QUICK_EDIT}"' in form
        assert hidden_fields(form)["_next_form_origin"] == change_url(book)
        assert 'value="Dracula"' in form
        assert "Save and continue editing" not in html
        assert "Add chapter" not in html
        assert f'data-next-layer="{HISTORY_PEEK}"' in html

    def test_the_quick_editor_titles_the_tab_after_the_row(
        self, admin_client, make_book
    ):
        r = admin_client.get_zones(change_url(make_book("Dracula")), QUICK_EDIT)
        assert meta_op(r) == {
            "op": "meta",
            "title": "Quick edit · Dracula · next.dj admin",
            "description": None,
            "canonical": None,
            "robots": "noindex, nofollow",
        }

    def test_the_quick_editor_is_never_cached(self, admin_client, book):
        r = admin_client.get_zones(change_url(book), QUICK_EDIT)
        assert r["Cache-Control"] == "private, no-store"
        assert "X-Next-Zone" in r["Vary"]

    def test_a_plain_visit_renders_the_real_change_page(self, admin_client, book):
        r = admin_client.get(change_url(book))
        body = r.content.decode()
        assert 'data-next-zone="record"' in body
        assert f'data-next-zone="{QUICK_EDIT}"' not in body
        assert "Save and continue editing" in body
        assert "Add chapter" in body
        assert_metadata(r, title="Books · next.dj admin")

    def test_the_record_zone_stays_the_change_page_own(self, admin_client, book):
        r = admin_client.get_zones(change_url(book), "record")
        assert envelope_of(r).op_verbs() == ["morph"]
        form = action_form(
            admin_client, envelope_of(r).html_for_zone("record"), "admin:change"
        )
        assert 'data-next-target="record"' in form

    def test_an_invalid_save_rerenders_the_quick_editor(self, admin_client, make_book):
        book = make_book("Dracula")
        r = quick_save(admin_client, book, title="")
        assert r.status_code == 200
        assert r["X-Next-Form"] == "invalid"
        assert envelope_of(r).zone_targets() == [QUICK_EDIT]
        html = envelope_of(r).html_for_zone(QUICK_EDIT)
        assert REQUIRED in html
        assert "Quick edit" in html
        book.refresh_from_db()
        assert book.title == "Dracula"

    def test_a_valid_save_closes_the_editor_as_accepted(self, admin_client, make_book):
        book = make_book("Dracula")
        r = quick_save(admin_client, book, title="Dracula, revised")
        envelope = envelope_of(r)
        assert envelope.op_verbs() == ["layer.close", "toast"]
        assert envelope.ops[0]["result"] == {"pk": book.pk}
        assert envelope.toasts()[0]["text"] == (
            "The book Dracula, revised was updated successfully."
        )
        book.refresh_from_db()
        assert book.title == "Dracula, revised"
        later = admin_client.get("/admin/library/book/").content.decode()
        assert "was updated successfully" not in later

    def test_a_save_without_the_runtime_still_redirects(self, admin_client, book):
        r = admin_client.post_action(
            "admin:change", _book_payload(book, title="Plain"), origin=change_url(book)
        )
        assert r.status_code == 302
        assert r["Location"] == "/admin/library/book/"

    def test_the_editor_reads_a_published_book_without_save(
        self, editor_client, make_book
    ):
        published = make_book("Dracula", status=Book.PUBLISHED)
        r = editor_client.get_zones(change_url(published), QUICK_EDIT)
        html = envelope_of(r).html_for_zone(QUICK_EDIT)
        assert "data-read-only" in html
        assert ">Save<" not in html

    def test_the_editor_cannot_save_a_published_book(self, editor_client, make_book):
        published = make_book("Dracula", status=Book.PUBLISHED)
        assert quick_save(editor_client, published, title="X").status_code == 403
        published.refresh_from_db()
        assert published.title == "Dracula"

    def test_an_anonymous_zone_request_redirects_to_the_login(self, next_client, book):
        r = next_client.get_zones(change_url(book), QUICK_EDIT)
        assert r.status_code == 302
        assert r["Location"] == f"/admin/login/?next={change_url(book)}"


class TestHistoryPeek:
    """`[int:pk]/change/(..)history/` peeks at the history from the change page."""

    def test_the_change_page_links_the_peek(self, admin_client, book):
        body = admin_client.get(change_url(book)).content.decode()
        link = find_anchor(body, href=history_url(book), text="History")
        assert f'data-next-layer="{HISTORY_PEEK}"' in link

    def test_the_add_page_offers_no_history(self, admin_client):
        body = admin_client.get("/admin/library/book/add/").content.decode()
        assert HISTORY_PEEK not in body

    def test_a_zone_request_serves_the_latest_entries(
        self, admin_client, admin_user, make_book
    ):
        book = make_book("Dracula")
        log_changes(admin_user, book, 7)
        r = admin_client.get_zones(history_url(book), HISTORY_PEEK)
        assert r.status_code == 200
        html = envelope_of(r).html_for_zone(HISTORY_PEEK)
        assert "Revision 7" in html
        assert "Revision 3" in html
        assert "Revision 2" not in html
        assert "2 older entries not shown" in html
        assert find_anchor(html, href=history_url(book), text="Open the full history")
        assert meta_op(r)["title"] == "History · Dracula · next.dj admin"

    def test_one_hidden_entry_reads_in_the_singular(
        self, admin_client, admin_user, book
    ):
        log_changes(admin_user, book, 6)
        r = admin_client.get_zones(history_url(book), HISTORY_PEEK)
        assert "1 older entry not shown" in envelope_of(r).html_for_zone(HISTORY_PEEK)

    def test_an_empty_history_says_so(self, admin_client, book):
        r = admin_client.get_zones(history_url(book), HISTORY_PEEK)
        html = envelope_of(r).html_for_zone(HISTORY_PEEK)
        assert "No history yet" in html
        assert "not shown" not in html

    def test_a_plain_visit_renders_the_full_history(
        self, admin_client, admin_user, book
    ):
        log_changes(admin_user, book, 7)
        body = admin_client.get(history_url(book)).content.decode()
        assert "Revision 1" in body
        assert f'data-next-zone="{HISTORY_PEEK}"' not in body

    def test_a_quick_save_shows_up_in_the_peek(self, admin_client, make_book):
        book = make_book("Dracula")
        quick_save(admin_client, book, title="Dracula, revised")
        r = admin_client.get_zones(history_url(book), HISTORY_PEEK)
        assert "Changed via shadcn-admin form" in envelope_of(r).html_for_zone(
            HISTORY_PEEK
        )

    def test_a_hidden_row_is_not_found(self, admin_client):
        r = admin_client.get_zones("/admin/library/book/999/history/", HISTORY_PEEK)
        assert r.status_code == 404


def peek(client: NextClient, book: Book):
    return client.get_zones(manuscript_url(book), MANUSCRIPT_PEEK)


class TestManuscriptPeek:
    """`(.)manuscript/` asks `view_book` and its target asks `change_book`, both run."""

    def test_the_outline_links_the_peek_beside_the_manuscript(
        self, editor_client, editor, make_book
    ):
        draft = make_book(status=Book.DRAFT, editor=editor)
        body = editor_client.get(outline_url(draft)).content.decode()
        link = find_anchor(
            body, href=manuscript_url(draft), text="Peek at the manuscript"
        )
        assert f'data-next-layer="{MANUSCRIPT_PEEK}"' in link

    def test_the_editor_peeks_at_an_own_draft(
        self, editor_client, editor, make_book, make_chapter
    ):
        draft = make_book("Mathilda", status=Book.DRAFT, editor=editor, summary="Held")
        make_chapter(draft, 1, "Intro", 100)
        make_chapter(draft, 2, "Rising", 250)
        r = peek(editor_client, draft)
        assert r.status_code == 200
        html = envelope_of(r).html_for_zone(MANUSCRIPT_PEEK)
        assert "data-peek-chapters>2<" in html
        assert "data-peek-words>350<" in html
        assert "2. Rising" in html
        assert "Held" in html
        assert APPEND not in html
        assert meta_op(r)["title"] == "Manuscript peek · Mathilda · next.dj admin"

    def test_an_empty_draft_peeks_as_empty(self, editor_client, editor, make_book):
        r = peek(editor_client, make_book(status=Book.DRAFT, editor=editor))
        html = envelope_of(r).html_for_zone(MANUSCRIPT_PEEK)
        assert "No chapters yet." in html
        assert "data-peek-words>0<" in html

    @pytest.mark.parametrize(
        ("status", "owner", "page_status"),
        [
            (Book.PUBLISHED, "editor", 403),
            (Book.DRAFT, None, 200),
            (Book.DRAFT, "copyeditor", 403),
        ],
        ids=["target-refuses", "intercept-refuses", "both-refuse"],
    )
    def test_each_chain_refuses_on_its_own(
        self,
        request,
        editor_client,
        make_book,
        make_chapter,
        status,
        owner,
        page_status,
    ):
        editor = owner and request.getfixturevalue(owner)
        target = make_book(status=status, editor=editor)
        make_chapter(target, title="Secret")
        r = peek(editor_client, target)
        assert r.status_code == 403
        assert b"Secret" not in r.content
        assert editor_client.get(manuscript_url(target)).status_code == page_status

    def test_an_intercept_refusal_is_never_cached(self, editor_client, make_book):
        r = peek(editor_client, make_book(status=Book.DRAFT))
        assert r.status_code == 403
        assert r["Cache-Control"] == "private, no-store"
        assert "X-Next-Zone" in r["Vary"]

    def test_the_superuser_peeks_at_a_published_book(
        self, admin_client, copyeditor, make_book
    ):
        published = make_book("Dracula", status=Book.PUBLISHED, editor=copyeditor)
        assert peek(admin_client, published).status_code == 200

    def test_an_anonymous_peek_redirects_to_the_login(self, next_client, book):
        r = peek(next_client, book)
        assert r.status_code == 302
        assert r["Location"] == f"/admin/login/?next={manuscript_url(book)}"

    def test_the_permission_table_lists_the_peek_after_its_page(self):
        rows = permission_rows("/admin/library/book/1/manuscript/")
        endpoint = f"/admin/library/book/<int:book>/manuscript/ ({MANUSCRIPT_PEEK})"
        assert rows[endpoint] == (
            "intercept",
            "login, perm shadcn_admin.use_admin, perm library.view_book on library.Book",
            f"{LIBRARY_RULE}, library/pages/library/book/[int:book]/(.)manuscript/page.py",
        )
