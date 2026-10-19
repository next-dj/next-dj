from pathlib import Path

import pytest

from next.permissions import PermissionAlreadyRegisteredError
from next.permissions.markers import OPEN, AccessRule, Predicate, Requirement, predicate
from next.permissions.registry import DeclarationRegistry, PermissionRegistry
from tests.support import is_owner, is_staff


@pytest.fixture()
def registry() -> PermissionRegistry:
    return PermissionRegistry()


class TestAdd:
    """`add` binds a predicate to a name and hands it back as a `Predicate`."""

    def test_a_predicate_is_stored_as_given(self, registry) -> None:
        rule = predicate(is_owner)
        assert registry.add("blog.change_post", rule) is rule
        assert registry.get("blog.change_post") is rule

    def test_a_plain_callable_is_wrapped_into_a_predicate(self, registry) -> None:
        rule = registry.add("blog.change_post", is_owner)
        assert isinstance(rule, Predicate)
        assert rule.test is is_owner
        assert rule.name == "is_owner"
        assert registry.get("blog.change_post") is rule

    def test_membership_and_length(self, registry) -> None:
        registry.add("blog.change_post", is_owner)
        registry.add("blog.delete_post", is_staff)
        assert "blog.change_post" in registry
        assert "blog.publish_post" not in registry
        assert len(registry) == 2

    def test_items_follow_registration_order(self, registry) -> None:
        second = registry.add("blog.z_post", is_staff)
        first = registry.add("blog.a_post", is_owner)
        assert list(registry.items()) == [
            ("blog.z_post", second),
            ("blog.a_post", first),
        ]

    def test_get_of_an_unknown_name_is_none(self, registry) -> None:
        assert registry.get("blog.change_post") is None

    def test_a_new_name_bumps_the_version(self, registry) -> None:
        before = registry.version
        registry.add("blog.change_post", is_owner)
        assert registry.version == before + 1


class TestMalformedNames:
    """A name Django cannot address is refused before anything is stored."""

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("change_post", id="no-dot"),
            pytest.param("blog.posts.change", id="two-dots"),
            pytest.param(".change_post", id="empty-app-label"),
            pytest.param("blog.", id="empty-codename"),
            pytest.param("", id="empty"),
        ],
    )
    def test_raises_value_error(self, registry, name: str) -> None:
        with pytest.raises(ValueError, match=r"app_label\.codename"):
            registry.add(name, is_owner)
        assert len(registry) == 0
        assert registry.version == 0


class TestRepeatedAdd:
    """The same rule again changes nothing, a different one is refused."""

    def test_the_same_predicate_is_a_no_op(self, registry) -> None:
        rule = registry.add("blog.change_post", is_owner)
        version = registry.version
        assert registry.add("blog.change_post", rule) is rule
        assert registry.version == version
        assert len(registry) == 1

    def test_an_equal_predicate_over_the_same_callable_is_a_no_op(
        self, registry
    ) -> None:
        held = registry.add("blog.change_post", is_owner)
        version = registry.version
        assert registry.add("blog.change_post", predicate(is_owner)) is held
        assert registry.add("blog.change_post", is_owner) is held
        assert registry.version == version

    def test_a_different_predicate_raises(self, registry) -> None:
        held = registry.add("blog.change_post", is_owner)
        with pytest.raises(PermissionAlreadyRegisteredError) as caught:
            registry.add("blog.change_post", is_staff)
        assert caught.value.name == "blog.change_post"
        assert "'blog.change_post'" in str(caught.value)
        assert registry.get("blog.change_post") is held


class TestReset:
    """`reset` drops every permission and bumps the version."""

    def test_reset_empties_and_bumps(self, registry) -> None:
        registry.add("blog.change_post", is_owner)
        version = registry.version
        registry.reset()
        assert len(registry) == 0
        assert list(registry.items()) == []
        assert registry.get("blog.change_post") is None
        assert registry.version == version + 1

    def test_a_name_registers_again_after_reset(self, registry) -> None:
        registry.add("blog.change_post", is_owner)
        registry.reset()
        rule = registry.add("blog.change_post", is_staff)
        assert registry.get("blog.change_post") is rule


_PAGE = Path("/tree/page.py")

_PROXY = Path("/tree/proxy.py")

_LOGIN = Requirement("login", _PAGE)


@pytest.fixture()
def declarations() -> DeclarationRegistry:
    return DeclarationRegistry()


class TestDeclarationRegistry:
    """Each file keeps its own declarations and a stamp of its last change."""

    def test_an_undeclared_file_reads_open(self, declarations) -> None:
        assert declarations.declared(_PAGE) is OPEN
        assert declarations.stamps([_PAGE]) == (None,)
        assert declarations.files() == ()

    def test_a_declaration_stamps_its_file_alone(self, declarations) -> None:
        declarations.declare(_PAGE, _LOGIN)

        assert declarations.declared(_PAGE) == AccessRule((_LOGIN,), sources=(_PAGE,))
        assert declarations.stamps([_PAGE, _PROXY]) == (1, None)
        assert declarations.version == 1
        assert declarations.files() == (_PAGE,)

    def test_a_repeated_declaration_changes_nothing(self, declarations) -> None:
        declarations.declare(_PAGE, _LOGIN)
        declarations.declare(_PAGE, _LOGIN)
        declarations.declare_public(_PAGE)
        declarations.declare_public(_PAGE)

        assert declarations.declared(_PAGE) == AccessRule((_LOGIN,), True, (_PAGE,))
        assert declarations.version == 2

    def test_forget_drops_the_file_and_restamps_it(self, declarations) -> None:
        declarations.declare(_PAGE, _LOGIN)
        declarations.forget(_PAGE)
        declarations.forget(_PROXY)

        assert declarations.declared(_PAGE) is OPEN
        assert declarations.stamps([_PAGE, _PROXY]) == (2, None)
        assert declarations.version == 2

    def test_reset_restamps_every_file_past_the_last_change(self, declarations) -> None:
        declarations.declare(_PAGE, _LOGIN)
        declarations.declare_public(_PROXY)

        declarations.reset()

        assert declarations.files() == ()
        assert declarations.stamps([_PAGE, _PROXY]) == (3, 3)
        assert declarations.version == 3


_PERM = Requirement("permission", _PAGE, ("auth.view_user",))


def _fail_after_declaring(declarations: DeclarationRegistry) -> None:
    with declarations.collecting(_PAGE):
        declarations.declare(_PAGE, _LOGIN)
        raise RuntimeError


class TestCollecting:
    """An execution gathers its declarations apart and swaps them in when it ends."""

    def test_a_reader_sees_the_earlier_rule_until_the_end(self, declarations) -> None:
        declarations.declare(_PAGE, _LOGIN)
        earlier = declarations.declared(_PAGE)

        with declarations.collecting(_PAGE):
            declarations.declare(_PAGE, _PERM)
            declarations.declare(_PAGE, _PERM)
            declarations.declare_public(_PAGE)
            assert declarations.declared(_PAGE) is earlier
            assert declarations.version == 1

        assert declarations.declared(_PAGE) == AccessRule((_PERM,), True, (_PAGE,))
        assert declarations.stamps([_PAGE]) == (2,)

    def test_an_execution_declaring_nothing_drops_the_rule(self, declarations) -> None:
        declarations.declare(_PAGE, _LOGIN)

        with declarations.collecting(_PAGE):
            pass

        assert declarations.declared(_PAGE) is OPEN
        assert declarations.files() == ()
        assert declarations.version == 2

    def test_the_same_declarations_change_nothing(self, declarations) -> None:
        declarations.declare(_PAGE, _LOGIN)

        with declarations.collecting(_PAGE):
            declarations.declare(_PAGE, _LOGIN)
        with declarations.collecting(_PROXY):
            pass

        assert declarations.version == 1

    def test_a_failed_execution_keeps_what_it_declared(self, declarations) -> None:
        with pytest.raises(RuntimeError):
            _fail_after_declaring(declarations)

        assert declarations.declared(_PAGE).requirements == (_LOGIN,)

    def test_another_file_declares_at_once(self, declarations) -> None:
        with declarations.collecting(_PAGE):
            declarations.declare_public(_PROXY)
            assert declarations.declared(_PROXY).public is True
