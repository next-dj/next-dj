"""End-to-end checks for the shadcn admin example.

Each phase of the example has at least one happy-path test plus a
representative failure mode. Together they replace the manual curl/shell
checks used while building the example.
"""

import re

import pytest
from library.models import Author, Book, Chapter, Tag


pytestmark = pytest.mark.django_db


def _action_form_body(body: str, *, action_substring: str = "/_next/form/") -> str:
    """Return the inner HTML of the page's primary action form.

    Skips the logout sign-out form in the topbar (also a `/_next/form/` URL
    but with no formset) by picking the last matching form.
    """
    forms = re.findall(
        r'<form[^>]*action="([^"]+)"[^>]*>(.+?)</form>',
        body,
        re.DOTALL,
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
        r'<input[^>]*name="([^"]+)"(?:[^>]*value="([^"]*)")?[^>]*>',
        target,
    ):
        out[m.group(1)] = m.group(2) or ""
    return out


class TestDashboard:
    def test_unauthenticated_redirects_to_login(self, client):
        r = client.get("/admin/")
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
        # admin shell wrapper around the entire page
        assert body.count('class="flex min-h-screen w-full"') == 1
        # topbar with sign-out / breadcrumbs
        assert body.count('class="flex h-14 shrink-0') == 1
        # dashboard description (page_header) renders once
        assert body.count("Models registered with django.contrib.admin") == 1
        # each app appears in sidebar (once) + dashboard card (once)
        assert body.count("Authentication and Authorization") == 2
        assert body.count(">Library<") == 2


class TestAuth:
    def test_login_page_renders(self, client):
        r = client.get("/admin/login/")
        assert r.status_code == 200
        body = r.content.decode()
        assert 'name="username"' in body
        assert 'name="password"' in body

    def test_login_post_redirects_and_authenticates(self, client, admin_user):
        r = client.post_action(
            "admin:login",
            {"username": "admin", "password": "admin-pass", "next": "/admin/"},
        )
        assert r.status_code == 302
        assert r["Location"] == "/admin/"
        # session is logged in: subsequent request goes through
        r2 = client.get("/admin/")
        assert r2.status_code == 200

    def test_logout_clears_session(self, admin_client):
        # already logged in via fixture
        r = admin_client.post_action("admin:logout", {})
        assert r.status_code == 302
        # the next request is unauthenticated, so redirected back to login
        r2 = admin_client.get("/admin/")
        assert r2.status_code == 302
        assert r2["Location"].startswith("/admin/login/")

    def test_bad_credentials_redirect_with_error_flag(self, client, admin_user):
        r = client.post_action(
            "admin:login",
            {"username": "admin", "password": "wrong", "next": "/admin/"},
        )
        assert r.status_code == 302
        assert r["Location"] == "/admin/login/?error=1"


class TestChangelist:
    def test_changelist_lists_rows(self, admin_client):
        author = Author.objects.create(full_name="Ursula K. Le Guin")
        Book.objects.create(
            title="A Wizard of Earthsea", author=author, status="published"
        )
        r = admin_client.get("/admin/library/book/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "A Wizard of Earthsea" in body

    def test_search_filters_rows(self, admin_client):
        author = Author.objects.create(full_name="Ursula K. Le Guin")
        Book.objects.create(
            title="A Wizard of Earthsea", author=author, status="published"
        )
        Book.objects.create(title="The Dispossessed", author=author, status="published")
        r = admin_client.get("/admin/library/book/?q=Wizard")
        body = r.content.decode()
        assert "A Wizard of Earthsea" in body
        assert "The Dispossessed" not in body

    def test_sort_by_column_returns_200(self, admin_client):
        author = Author.objects.create(full_name="A. Author")
        Book.objects.create(title="Z book", author=author)
        Book.objects.create(title="A book", author=author)
        r = admin_client.get("/admin/library/book/?o=1")
        assert r.status_code == 200

    def test_list_filter_applies_status(self, admin_client):
        author = Author.objects.create(full_name="A. Author")
        Book.objects.create(title="Drafted", author=author, status="draft")
        Book.objects.create(title="Published", author=author, status="published")
        r = admin_client.get("/admin/library/book/?status__exact=published")
        body = r.content.decode()
        assert "Published" in body
        assert "Drafted" not in body

    @pytest.mark.parametrize(
        "path",
        [
            "/admin/library/nonexistent/",
            "/admin/nonexistent_app/foo/",
            # `auth.Permission` exists but `admin.site` does not register it.
            "/admin/auth/permission/",
        ],
        ids=("unknown_model", "unknown_app", "unregistered_model"),
    )
    def test_changelist_unknown_target_returns_404(self, admin_client, path):
        assert admin_client.get(path).status_code == 404

    def test_renders_none_value_as_dash(self, admin_client):
        Author.objects.create(full_name="Anon", email="a@a.com", born_in=None)
        r = admin_client.get("/admin/library/author/")
        assert r.status_code == 200
        assert "&mdash;" in r.content.decode()

    def test_changelist_title_is_capitalized(self, admin_client):
        r = admin_client.get("/admin/library/tag/")
        body = r.content.decode()
        # `verbose_name_plural` is lowercase by Django convention, but the
        # changelist page header capitalises it.
        assert ">Tags<" in body

    def test_changelist_without_filters_omits_sidebar(self, admin_client):
        # Tag admin has no `list_filter`, so the filters panel must not render.
        r = admin_client.get("/admin/library/tag/")
        body = r.content.decode()
        assert "Filters" not in body
        # filters_panel adds a `md:w-60` aside; without filters it is absent.
        assert "md:w-60" not in body

    def test_pagination_links_render_on_overflow(self, admin_client):
        for i in range(120):
            Tag.objects.create(name=f"t{i:03d}", slug=f"t{i:03d}")
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
            "admin:bulk_action",
            {
                "_url_param_app_label": "library",
                "_url_param_model_name": "book",
                "action": "",
            },
        )
        # response_action returns None when nothing is selected — handler redirects.
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
            {
                "_url_param_app_label": "library",
                "_url_param_model_name": "tag",
                "name": "SciFi",
                "slug": "scifi",
            },
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
        assert not Tag.objects.exists()


class TestChangeView:
    def test_change_get_renders_form(self, admin_client):
        tag = Tag.objects.create(name="Old", slug="old")
        r = admin_client.get(f"/admin/library/tag/{tag.pk}/change/")
        assert r.status_code == 200
        body = r.content.decode()
        assert 'value="Old"' in body
        assert 'value="old"' in body

    def test_change_post_updates_record(self, admin_client):
        tag = Tag.objects.create(name="Old", slug="old")
        r = admin_client.post_action(
            "admin:change",
            {
                "_url_param_app_label": "library",
                "_url_param_model_name": "tag",
                "_url_param_pk": str(tag.pk),
                "name": "New",
                "slug": "new",
            },
        )
        assert r.status_code == 302
        tag.refresh_from_db()
        assert tag.name == "New"
        assert tag.slug == "new"


class TestDeleteView:
    def test_delete_get_renders_confirmation(self, admin_client):
        tag = Tag.objects.create(name="Doomed", slug="doomed")
        r = admin_client.get(f"/admin/library/tag/{tag.pk}/delete/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "Yes, delete" in body
        assert "Doomed" in body

    def test_delete_post_removes_record(self, admin_client):
        tag = Tag.objects.create(name="Doomed", slug="doomed")
        r = admin_client.post_action(
            "admin:delete",
            {
                "_url_param_app_label": "library",
                "_url_param_model_name": "tag",
                "_url_param_pk": str(tag.pk),
            },
        )
        assert r.status_code == 302
        assert not Tag.objects.filter(pk=tag.pk).exists()

    def test_delete_get_unknown_pk_404(self, admin_client):
        r = admin_client.get("/admin/library/tag/99999/delete/")
        assert r.status_code == 404

    def test_delete_post_unknown_pk_404(self, admin_client):
        r = admin_client.post_action(
            "admin:delete",
            {
                "_url_param_app_label": "library",
                "_url_param_model_name": "tag",
                "_url_param_pk": "99999",
            },
        )
        assert r.status_code == 404


class TestInlines:
    def test_change_book_renders_chapter_inlines(self, admin_client):
        author = Author.objects.create(full_name="A. Author")
        book = Book.objects.create(title="With chapters", author=author)
        Chapter.objects.create(book=book, number=1, title="Intro", word_count=100)
        r = admin_client.get(f"/admin/library/book/{book.pk}/change/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "Intro" in body
        assert "chapters" in body.lower()

    def test_change_book_autocomplete_field_renders_select(self, admin_client):
        author = Author.objects.create(full_name="A. Author")
        book = Book.objects.create(title="With autocomplete", author=author)
        r = admin_client.get(f"/admin/library/book/{book.pk}/change/")
        assert r.status_code == 200
        body = r.content.decode()
        # autocomplete_fields = ("author",) should render as a <select>
        assert 'name="author"' in body
        assert '<select name="author"' in body

    def test_add_book_through_browser_flow(self, admin_client):
        """Mirrors a browser: GET the form, copy every rendered input, POST.

        Catches regressions where the GET page omits a hidden field the
        dispatcher relies on (csrf, _next_form_page, _url_param_*), and where
        the rendered initial values for an unfilled `extra` inline row would
        make the formset look "changed" and trigger validation against
        otherwise-skipped empty required fields.
        """
        author = Author.objects.create(full_name="A. Author")
        tag = Tag.objects.create(name="Fantasy", slug="fantasy")

        get = admin_client.get("/admin/library/book/add/")
        assert get.status_code == 200
        rendered = _extract_form_inputs(get.content.decode())

        assert "csrfmiddlewaretoken" in rendered
        assert "_next_form_page" in rendered
        assert rendered.get("_url_param_app_label") == "library"
        assert rendered.get("_url_param_model_name") == "book"

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

    def test_change_book_with_inline_create(self, admin_client):
        author = Author.objects.create(full_name="A. Author")
        book = Book.objects.create(title="Book", author=author)
        # Get the change page to discover formset management form values
        get = admin_client.get(f"/admin/library/book/{book.pk}/change/")
        hiddens = _extract_form_hiddens(get.content.decode())
        # The default ChapterInline has extra=1, so we have 0 existing + 1 extra row
        payload = {
            **hiddens,
            "title": "Book",
            "author": str(author.pk),
            "status": "draft",
            "summary": "",
            "price": "0",
            "chapters-TOTAL_FORMS": "1",
            "chapters-INITIAL_FORMS": "0",
            "chapters-MIN_NUM_FORMS": "0",
            "chapters-MAX_NUM_FORMS": "1000",
            "chapters-0-number": "1",
            "chapters-0-title": "Chapter one",
            "chapters-0-word_count": "100",
        }
        r = admin_client.post_action("admin:change", payload)
        assert r.status_code == 302
        assert Chapter.objects.filter(book=book, number=1, title="Chapter one").exists()


class TestHistoryView:
    def test_history_renders_after_change(self, admin_client):
        tag = Tag.objects.create(name="Old", slug="old")
        admin_client.post_action(
            "admin:change",
            {
                "_url_param_app_label": "library",
                "_url_param_model_name": "tag",
                "_url_param_pk": str(tag.pk),
                "name": "New",
                "slug": "new",
            },
        )
        r = admin_client.get(f"/admin/library/tag/{tag.pk}/history/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "Changed" in body
        assert "admin" in body  # username

    def test_history_unknown_pk_404(self, admin_client):
        r = admin_client.get("/admin/library/tag/99999/history/")
        assert r.status_code == 404


class TestInlineValidationFailure:
    """Inline formset with a partially-filled row fails validation on both flows.

    Filling `chapters-0-number` makes the row "intent to save", so the empty
    required `chapters-0-title` flips the formset to invalid. The handler
    answers 400 from the same code path on add and change.
    """

    @pytest.mark.parametrize(
        ("flow", "action_name"),
        [("add", "admin:add"), ("change", "admin:change")],
        ids=("add", "change"),
    )
    def test_inline_invalid_returns_400(self, admin_client, flow, action_name):
        author = Author.objects.create(full_name="A. Author")
        if flow == "add":
            get_url = "/admin/library/book/add/"
        else:
            book = Book.objects.create(title="Book", author=author)
            get_url = f"/admin/library/book/{book.pk}/change/"

        get = admin_client.get(get_url)
        rendered = _extract_form_inputs(get.content.decode())
        payload = {
            **rendered,
            "title": "Book with bad chapter",
            "author": str(author.pk),
            "status": "draft",
            "summary": "",
            "price": "0",
            "chapters-0-number": "1",
            "chapters-0-title": "",  # required, empty → invalid
            "chapters-0-word_count": "10",
        }
        r = admin_client.post_action(action_name, payload)
        assert r.status_code == 400
        if flow == "add":
            assert not Book.objects.filter(title="Book with bad chapter").exists()
