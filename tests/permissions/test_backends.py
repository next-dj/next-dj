from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import aauthenticate, authenticate, get_user_model
from django.contrib.auth.models import Group, Permission

from next.permissions import ObjectPermissionBackend, add_perm
from tests.support import owns_group


User = get_user_model()

pytestmark = [
    pytest.mark.django_db(),
    pytest.mark.usefixtures("object_permission_backend"),
]


def _member(username: str = "ada", **fields: object):
    return User.objects.create_user(username, **fields)


@pytest.fixture()
def owned_rules() -> None:
    add_perm("auth.edit_group", owns_group)
    add_perm("auth.see_group", lambda: True)
    add_perm("blog.read_post", lambda user, obj: user.is_active and obj is None)


class TestHasPerm:
    """`user.has_perm` reaches the predicate through the configured backend."""

    @pytest.mark.usefixtures("owned_rules")
    def test_the_owner_holds_it_on_the_object(self) -> None:
        user = _member("ada")
        assert user.has_perm("auth.edit_group", Group(name="ada")) is True

    @pytest.mark.usefixtures("owned_rules")
    def test_another_user_does_not(self) -> None:
        user = _member("bob")
        assert user.has_perm("auth.edit_group", Group(name="ada")) is False

    @pytest.mark.usefixtures("owned_rules")
    def test_without_an_object_the_predicate_gets_none(self) -> None:
        user = _member()
        assert user.has_perm("auth.edit_group") is False
        assert user.has_perm("blog.read_post") is True

    @pytest.mark.usefixtures("owned_rules")
    def test_an_unregistered_permission_is_refused(self) -> None:
        assert _member().has_perm("blog.burn_post") is False

    def test_a_model_permission_still_comes_from_model_backend(self) -> None:
        user = _member()
        user.user_permissions.add(Permission.objects.get(codename="add_group"))
        user = User.objects.get(pk=user.pk)
        assert user.has_perm("auth.add_group") is True
        assert user.has_perm("auth.add_group", Group(name="x")) is False

    def test_a_superuser_never_reaches_the_predicate(self) -> None:
        calls: list[object] = []
        add_perm("auth.edit_group", lambda: calls.append(True) or False)
        root = User.objects.create_superuser("root", "root@example.com")
        assert root.has_perm("auth.edit_group", Group(name="x")) is True
        assert calls == []

    @pytest.mark.usefixtures("owned_rules")
    def test_an_inactive_user_holds_nothing(self) -> None:
        user = _member("ada", is_active=False)
        assert user.has_perm("auth.edit_group", Group(name="ada")) is False
        assert user.has_perm("auth.see_group") is False


class TestAllPermissions:
    """`get_all_permissions` joins the predicates to the model permissions."""

    @pytest.mark.usefixtures("owned_rules")
    def test_with_an_object(self) -> None:
        user = _member("ada")
        assert user.get_all_permissions(Group(name="ada")) == {
            "auth.edit_group",
            "auth.see_group",
        }

    @pytest.mark.usefixtures("owned_rules")
    def test_without_an_object(self) -> None:
        user = _member()
        user.user_permissions.add(Permission.objects.get(codename="add_group"))
        user = User.objects.get(pk=user.pk)
        assert user.get_all_permissions() == {
            "auth.add_group",
            "auth.see_group",
            "blog.read_post",
        }

    def test_the_backend_returns_a_set(self) -> None:
        held = ObjectPermissionBackend().get_all_permissions(_member())
        assert held == set()
        assert type(held) is set


class TestModulePerms:
    """`has_module_perms` holds for a label one registered permission grants."""

    @pytest.mark.usefixtures("owned_rules")
    def test_a_label_with_a_held_permission(self) -> None:
        assert _member().has_module_perms("blog") is True

    def test_a_label_with_no_held_permission(self) -> None:
        add_perm("blog.read_post", lambda: False)
        assert _member().has_module_perms("blog") is False


class TestAuthenticate:
    """The backend authenticates nobody, so another backend logs the user in."""

    def test_returns_none(self) -> None:
        assert ObjectPermissionBackend().authenticate(None, username="ada") is None

    def test_model_backend_still_logs_in(self) -> None:
        user = _member("ada", password="pw")
        assert authenticate(None, username="ada", password="pw") == user

    def test_async_returns_none(self) -> None:
        backend = ObjectPermissionBackend()
        assert async_to_sync(backend.aauthenticate)(None, username="ada") is None

    def test_async_model_backend_still_logs_in(self) -> None:
        user = _member("ada", password="pw")
        found = async_to_sync(aauthenticate)(None, username="ada", password="pw")
        assert found == user


class TestAsyncTwins:
    """The async twins give the answers the sync methods give."""

    @pytest.mark.usefixtures("owned_rules")
    def test_ahas_perm(self) -> None:
        user = _member("ada")
        assert async_to_sync(user.ahas_perm)("auth.edit_group", Group(name="ada"))
        assert not async_to_sync(user.ahas_perm)("auth.edit_group", Group(name="bob"))

    @pytest.mark.usefixtures("owned_rules")
    def test_aget_all_permissions(self) -> None:
        user = _member("ada")
        held = async_to_sync(user.aget_all_permissions)(Group(name="ada"))
        assert held == {"auth.edit_group", "auth.see_group"}

    @pytest.mark.usefixtures("owned_rules")
    def test_ahas_module_perms(self) -> None:
        user = _member()
        assert async_to_sync(user.ahas_module_perms)("blog") is True
        assert async_to_sync(user.ahas_module_perms)("shop") is False

    @pytest.mark.usefixtures("owned_rules")
    def test_the_backend_twins_match_the_sync_answers(self) -> None:
        backend = ObjectPermissionBackend()
        user = SimpleNamespace(is_active=True, username="ada")
        group = Group(name="ada")
        assert async_to_sync(backend.ahas_perm)(user, "auth.edit_group", group) is True
        assert async_to_sync(backend.ahas_module_perms)(user, "auth") is True
        held = async_to_sync(backend.aget_all_permissions)(user, group)
        assert held == backend.get_all_permissions(user, group)
        assert type(held) is set
