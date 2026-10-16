import pytest

from next.permissions import PermissionAlreadyRegisteredError, Predicate, predicate
from next.permissions.registry import PermissionRegistry
from next.permissions.signals import permission_registered
from next.testing import capture_signals
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

    def test_a_plain_callable_is_wrapped_into_a_leaf(self, registry) -> None:
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

    def test_an_equal_leaf_over_the_same_callable_is_a_no_op(self, registry) -> None:
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


class TestPermissionRegisteredSignal:
    """`permission_registered` announces a new name once, a no-op stays silent."""

    def test_sent_once_for_a_new_name(self, registry) -> None:
        with capture_signals(permission_registered) as recorder:
            rule = registry.add("blog.change_post", is_owner)
        assert len(recorder) == 1
        event = recorder.first_for(permission_registered)
        assert event.sender is PermissionRegistry
        assert event.kwargs == {"name": "blog.change_post", "predicate": rule}

    def test_silent_for_a_repeated_add(self, registry) -> None:
        registry.add("blog.change_post", is_owner)
        with capture_signals(permission_registered) as recorder:
            registry.add("blog.change_post", is_owner)
        assert len(recorder) == 0

    def test_silent_for_a_refused_add(self, registry) -> None:
        registry.add("blog.change_post", is_owner)
        with capture_signals(permission_registered) as recorder:
            with pytest.raises(PermissionAlreadyRegisteredError):
                registry.add("blog.change_post", is_staff)
            with pytest.raises(ValueError, match=r"app_label\.codename"):
                registry.add("nodot", is_owner)
        assert len(recorder) == 0
