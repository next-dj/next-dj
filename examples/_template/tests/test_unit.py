import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from myapp.models import Item
from myapp.permissions import is_owner, is_signed_in
from myapp.services import create_item, rename_item
from myapp.signals import item_created

from next.permissions import has_perm
from next.testing import capture_signals


pytestmark = pytest.mark.django_db


class TestCreateItem:
    def test_the_item_belongs_to_the_user(self, alice) -> None:
        assert create_item(alice, title="First").owner == alice

    def test_the_new_item_is_announced(self, alice) -> None:
        with capture_signals(item_created) as recorder:
            item = create_item(alice, title="First")
        assert [event.kwargs["item"] for event in recorder] == [item]

    def test_an_empty_title_is_refused(self, alice) -> None:
        with pytest.raises(ValidationError):
            create_item(alice, title="")
        assert not Item.objects.exists()


class TestRenameItem:
    def test_the_owner_renames_the_item(self, alice, mine) -> None:
        rename_item(alice, item_id=mine.pk, title="Second")
        mine.refresh_from_db()
        assert mine.title == "Second"

    def test_a_stranger_cannot_reach_the_item(self, bob, mine) -> None:
        with pytest.raises(Item.DoesNotExist):
            rename_item(bob, item_id=mine.pk, title="Second")


class TestVisibleTo:
    def test_a_user_sees_only_their_items(self, alice, mine, theirs) -> None:
        assert list(Item.objects.visible_to(alice)) == [mine]

    def test_a_superuser_sees_every_item(self, superuser, mine, theirs) -> None:
        assert list(Item.objects.visible_to(superuser)) == [theirs, mine]

    def test_an_anonymous_visitor_sees_nothing(self, mine) -> None:
        assert not Item.objects.visible_to(AnonymousUser()).exists()

    def test_an_item_reads_as_its_title(self, mine) -> None:
        assert str(mine) == "Mine"


class TestPermissions:
    def test_adding_asks_only_for_a_signed_in_user(self, alice) -> None:
        assert is_signed_in(alice) is True
        assert is_signed_in(AnonymousUser()) is False
        assert has_perm(alice, "myapp.add_item") is True

    def test_changing_and_deleting_ask_for_the_owner(self, alice, bob, mine) -> None:
        assert is_owner(alice, mine) is True
        assert is_owner(bob, mine) is False
        assert is_owner(alice, None) is False
        for perm in ("myapp.change_item", "myapp.delete_item"):
            assert has_perm(alice, perm, mine) is True
            assert has_perm(bob, perm, mine) is False
