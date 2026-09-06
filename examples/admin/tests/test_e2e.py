import re
from dataclasses import dataclass

import pytest
from admin_audit.models import AdminActivityLog
from django.core.management import call_command
from library.demo import DEMO_BOOKS, seed_demo
from library.models import Book, Chapter, Tag

from next.testing import envelope_of


pytestmark = pytest.mark.django_db


def _action_form_body(body: str, *, action_substring: str = "/_next/form/") -> str:
    """Return the inner HTML of the page's primary action form.

    Skips the logout sign-out form in the topbar (also a `/_next/form/` URL
    but with no formset) by picking the last matching form.
    """
    forms = re.findall(
        r'<form[^>]*?(?<!-)action="([^"]+)"[^>]*>(.+?)</form>', body, re.DOTALL
    )
    matching = [text for url, text in forms if action_substring in url]
    return matching[-1] if matching else ""


def _extract_form_hiddens(body: str) -> dict[str, str]:
    """Return all hidden inputs of the primary action form."""
    target = _action_form_body(body)
    return dict(re.findall(r'name="([^"]+)"\s+value="([^"]*)"', target))


def _extract_form_inputs(body: str) -> dict[str, str]:
    """Return every input's name=value from the primary action form."""
    target = _action_form_body(body)
    out: dict[str, str] = {}
    for m in re.finditer(
        r'<input[^>]*name="([^"]+)"(?:[^>]*value="([^"]*)")?[^>]*>', target
    ):
        out[m.group(1)] = m.group(2) or ""
    return out


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

    def test_admin_chrome_renders_once(self, admin_client):
        """Each chrome piece — sidebar, topbar, app card — appears exactly once."""
        r = admin_client.get("/admin/")
        body = r.content.decode()
        assert body.count('class="flex min-h-screen w-full"') == 1
        assert body.count('class="flex h-14 shrink-0') == 1
        assert body.count("Models registered with django.contrib.admin") == 1
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
        rendered = _extract_form_inputs(
            next_client.get("/admin/login/").content.decode()
        )
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


class TestDemoSeed:
    """The demo catalog loads from a seed module, never from a migration."""

    def test_command_fills_the_catalog(self, db):
        call_command("seed_demo")
        assert Book.objects.count() == len(DEMO_BOOKS)
        assert Chapter.objects.filter(book__title="Frankenstein").count() == 4

    def test_seeding_twice_keeps_one_copy(self, demo_data):
        seed_demo()
        assert Book.objects.count() == len(DEMO_BOOKS)


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

        We render selection ourselves through `selectable=`, so the literal
        column header must not appear.
        """
        r = admin_client.get("/admin/library/tag/")
        body = r.content.decode()
        assert "ACTION CHECKBOX" not in body
        assert "action checkbox" not in body.lower()

    def test_delete_selected_description_is_interpolated(self, admin_client):
        """Django's `delete_selected` ships with `%(verbose_name_plural)s` placeholder."""
        r = admin_client.get("/admin/library/tag/")
        body = r.content.decode()
        assert "%(verbose_name_plural)s" not in body
        assert "Delete selected tags" in body


class TestBulkAction:
    def test_bulk_action_with_no_selection_redirects(self, admin_client):
        r = admin_client.post_action(
            "admin:bulk_action", {"action": ""}, origin="/admin/library/book/"
        )
        assert r.status_code == 302
        assert r["Location"] == "/admin/library/book/"


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
        hiddens = _extract_form_hiddens(get.content.decode())
        payload = {**hiddens, "name": "", "slug": ""}
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
        """Mirrors a browser: GET the form, copy every rendered input, POST.

        Catches regressions where the GET page omits a hidden field the
        dispatcher relies on (csrf, _next_form_origin), and where the
        rendered initial values for an unfilled `extra` inline row would
        make the formset look "changed" and trigger validation against
        otherwise-skipped empty required fields.
        """
        tag = make_tag("Fantasy")

        get = admin_client.get("/admin/library/book/add/")
        assert get.status_code == 200
        rendered = _extract_form_inputs(get.content.decode())

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

    def test_discard_form_is_scoped_to_the_dialog_by_css(
        self, admin_client, book_with_one_chapter
    ):
        _book, chapter = book_with_one_chapter
        response = admin_client.get(f"/admin/library/chapter/{chapter.pk}/change/")
        assert response.status_code == 200
        body = response.content.decode()
        assert 'class="discard-in-layer"' in body
        assert "/static/next/components/admin_form.css" in body

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
    """The add view's batch inline formset re-renders with errors on a bad row.

    Filling `chapters-0-number` makes the row "intent to save", so the empty
    required `chapters-0-title` flips the formset to invalid. `AdminForm.clean()`
    raises `ValidationError` and the framework re-renders the origin page with
    the bound form, so the user keeps their typed data and sees the row error.
    """

    def test_inline_invalid_rerenders_with_errors(self, admin_client, author):
        get = admin_client.get("/admin/library/book/add/")
        rendered = _extract_form_inputs(get.content.decode())
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
    """`_save_continue` and `_save_addanother` route to change/add instead of changelist."""

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
    """`mark_as_published` is registered on BookAdmin and reachable through admin:bulk_action."""

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
