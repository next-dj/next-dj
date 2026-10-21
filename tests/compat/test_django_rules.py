from collections.abc import Iterator

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings

from next.permissions import add_perm
from next.permissions.manager import permission_manager
from tests.support import owns_group


rules = pytest.importorskip("rules")

_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "rules.permissions.ObjectPermissionBackend",
    "next.permissions.ObjectPermissionBackend",
]
_RULES_PERM = "compat.read_group"
_NEXT_PERM = "compat.edit_group"


@pytest.fixture()
def both_backends(isolated_permissions) -> Iterator[None]:
    """Register one permission with each library and list both backends."""
    rules.add_perm(_RULES_PERM, rules.predicate(owns_group))
    add_perm(_NEXT_PERM, owns_group)
    try:
        with override_settings(AUTHENTICATION_BACKENDS=_BACKENDS):
            yield
    finally:
        rules.remove_perm(_RULES_PERM)


@pytest.mark.django_db()
@pytest.mark.usefixtures("both_backends")
class TestSideBySide:
    """A django-rules permission and a next permission answer one `has_perm`."""

    @pytest.mark.parametrize("perm", [_RULES_PERM, _NEXT_PERM])
    def test_the_owner_holds_both(self, perm: str) -> None:
        user = get_user_model().objects.create_user("ada")
        assert user.has_perm(perm, Group(name="ada")) is True

    @pytest.mark.parametrize("perm", [_RULES_PERM, _NEXT_PERM])
    def test_another_user_holds_neither(self, perm: str) -> None:
        user = get_user_model().objects.create_user("bob")
        assert user.has_perm(perm, Group(name="ada")) is False

    def test_known_sees_the_rules_permission(self) -> None:
        assert permission_manager.known(_RULES_PERM) is True
        assert permission_manager.known(_NEXT_PERM) is True
        assert permission_manager.known("compat.burn_group") is False

    def test_known_ignores_rules_once_its_backend_is_gone(self) -> None:
        with override_settings(AUTHENTICATION_BACKENDS=[_BACKENDS[0], _BACKENDS[2]]):
            assert permission_manager.known(_RULES_PERM) is False
