from io import StringIO

import pytest
from admin_audit.models import AdminActivityLog
from admin_audit.services import HistoryEntry, object_history
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from library.models import Book
from library.permissions import in_draft_book, is_draft, no_row, uses_admin
from shadcn_admin.permissions import is_staff

from next.permissions import has_perm


pytestmark = pytest.mark.django_db


class TestPredicates:
    def test_is_staff(self, editor, django_user_model) -> None:
        assert is_staff(editor) is True
        assert is_staff(django_user_model.objects.create_user("reader")) is False
        assert is_staff(AnonymousUser()) is False

    def test_uses_admin_asks_the_rule_of_the_admin(
        self, editor, admin_user, django_user_model
    ) -> None:
        assert uses_admin(editor) is True
        assert uses_admin(admin_user) is True
        assert uses_admin(django_user_model.objects.create_user("reader")) is False
        assert uses_admin(AnonymousUser()) is False

    def test_no_row(self, editor, book) -> None:
        assert no_row(editor, None) is True
        assert no_row(editor, book) is False
        assert no_row(AnonymousUser(), None) is True

    def test_is_draft(self, editor, make_book) -> None:
        assert is_draft(editor, make_book(status=Book.DRAFT)) is True
        assert is_draft(editor, make_book(status=Book.PUBLISHED)) is False
        assert is_draft(editor, None) is False
        assert is_draft(AnonymousUser(), make_book(status=Book.DRAFT)) is True

    def test_in_draft_book(self, editor, make_book, make_chapter) -> None:
        draft = make_chapter(make_book(status=Book.DRAFT))
        archived = make_chapter(make_book(status=Book.ARCHIVED))
        assert in_draft_book(editor, draft) is True
        assert in_draft_book(editor, archived) is False
        assert in_draft_book(editor, None) is False


class TestBookScope:
    def test_staff_see_every_book_and_nobody_else_any(
        self, editor, book, django_user_model
    ) -> None:
        reader = django_user_model.objects.create_user("reader")
        assert list(Book.objects.visible_to(editor)) == [book]
        assert not Book.objects.visible_to(reader).exists()
        assert not Book.objects.visible_to(AnonymousUser()).exists()


class TestActivityScope:
    def test_only_a_reader_of_the_audit_log_sees_its_entries(
        self, editor, admin_user
    ) -> None:
        entry = AdminActivityLog.objects.create(action="add", response_status=302)
        assert list(AdminActivityLog.objects.visible_to(admin_user)) == [entry]
        assert not AdminActivityLog.objects.visible_to(editor).exists()
        assert not AdminActivityLog.objects.visible_to(AnonymousUser()).exists()


class TestRegisteredRules:
    def test_the_admin_opens_to_staff_and_the_superuser(
        self, editor, admin_user, django_user_model
    ) -> None:
        assert has_perm(editor, "shadcn_admin.use_admin") is True
        assert has_perm(admin_user, "shadcn_admin.use_admin") is True
        reader = django_user_model.objects.create_user("reader")
        assert has_perm(reader, "shadcn_admin.use_admin") is False
        assert has_perm(AnonymousUser(), "shadcn_admin.use_admin") is False

    def test_an_editor_changes_and_deletes_only_draft_books(
        self, editor, make_book
    ) -> None:
        draft = make_book(status=Book.DRAFT)
        published = make_book(status=Book.PUBLISHED)
        for perm in ("library.change_book", "library.delete_book"):
            assert has_perm(editor, perm, draft) is True
            assert has_perm(editor, perm, published) is False
            assert has_perm(editor, perm, None) is True

    def test_chapters_follow_their_book(self, editor, make_book, make_chapter) -> None:
        draft = make_chapter(make_book(status=Book.DRAFT))
        published = make_chapter(make_book(status=Book.PUBLISHED))
        assert has_perm(editor, "library.change_chapter", draft) is True
        assert has_perm(editor, "library.change_chapter", published) is False
        assert has_perm(editor, "library.delete_chapter", published) is False

    def test_authors_and_tags_are_open_to_staff(self, editor, author) -> None:
        assert has_perm(editor, "library.change_author", author) is True
        assert has_perm(editor, "library.delete_tag", None) is True
        assert has_perm(editor, "library.add_book") is True

    def test_nobody_but_staff_holds_a_catalog_right(
        self, django_user_model, book
    ) -> None:
        reader = django_user_model.objects.create_user("reader")
        for user in (reader, AnonymousUser()):
            assert has_perm(user, "library.change_book", None) is False
            assert has_perm(user, "library.add_tag") is False


class TestAuditIsolation:
    def test_the_canvas_marks_the_audit_app_isolated(self) -> None:
        out = StringIO()
        call_command("next", "boundaries", "admin_audit", stdout=out)
        assert out.getvalue().splitlines()[:2] == [
            "admin_audit (admin_audit)",
            "Isolated: yes",
        ]

    def test_object_history_hands_out_history_entries(self, admin_user, book) -> None:
        logged = LogEntry.objects.create(
            user=admin_user,
            content_type=ContentType.objects.get_for_model(book),
            object_id=str(book.pk),
            object_repr=str(book),
            action_flag=CHANGE,
            change_message="Changed title.",
        )
        assert object_history(book) == [
            HistoryEntry(logged.action_time, "admin", "Changed", "Changed title.")
        ]
