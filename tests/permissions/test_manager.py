import sys
from types import ModuleType, SimpleNamespace

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser, Group
from django.test import override_settings
from django.utils.module_loading import import_string

from next import permissions
from next.permissions import manager as manager_module
from next.permissions.manager import PermissionManager, permission_manager
from next.permissions.markers import Predicate, predicate
from next.permissions.registry import PermissionRegistry
from tests.support import MODEL_BACKEND, is_owner, is_staff


_RULES_BACKEND = "rules.permissions.ObjectPermissionBackend"


def _user(*, active: bool = True, staff: bool = False) -> SimpleNamespace:
    return SimpleNamespace(is_active=active, is_staff=staff)


@pytest.fixture()
def manager() -> PermissionManager:
    return PermissionManager()


@pytest.fixture()
def rules_module(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Install a stand-in `rules.permissions` that knows one permission."""
    asked: list[str] = []

    def perm_exists(name: str) -> bool:
        asked.append(name)
        return name == "rules.read_book"

    package = ModuleType("rules")
    module = ModuleType("rules.permissions")
    module.perm_exists = perm_exists
    package.permissions = module
    monkeypatch.setitem(sys.modules, "rules", package)
    monkeypatch.setitem(sys.modules, "rules.permissions", module)
    return asked


class TestRegistryAccess:
    """The manager reads and writes its own registry, or the one it is given."""

    def test_a_given_registry_is_used(self) -> None:
        registry = PermissionRegistry()
        manager = PermissionManager(registry)
        manager.add("blog.change_post", is_owner)
        assert manager.registry is registry
        assert "blog.change_post" in registry

    def test_a_default_registry_is_its_own(self, manager) -> None:
        assert manager.registry is not PermissionManager().registry
        assert isinstance(manager.registry, PermissionRegistry)

    def test_add_stores_a_predicate_and_bumps_the_version(self, manager) -> None:
        before = manager.version
        manager.add("blog.change_post", is_owner)
        assert isinstance(manager.registry.get("blog.change_post"), Predicate)
        assert manager.version == before + 1
        assert manager.version == manager.registry.version

    def test_is_registered(self, manager) -> None:
        manager.add("blog.change_post", is_owner)
        assert manager.is_registered("blog.change_post") is True
        assert manager.is_registered("blog.delete_post") is False


class TestHasPerm:
    """`has_perm` asks the predicate for an active user only."""

    def test_an_active_user_the_predicate_allows(self, manager) -> None:
        manager.add("blog.change_post", is_owner)
        user = _user()
        assert manager.has_perm(user, "blog.change_post", SimpleNamespace(owner=user))

    def test_an_active_user_the_predicate_refuses(self, manager) -> None:
        manager.add("blog.change_post", is_owner)
        assert not manager.has_perm(_user(), "blog.change_post", SimpleNamespace())

    def test_obj_defaults_to_none(self, manager) -> None:
        seen: list[object] = []
        manager.add(
            "blog.view_post", lambda user, obj: seen.append((user, obj)) or True
        )
        user = _user()
        assert manager.has_perm(user, "blog.view_post") is True
        assert seen == [(user, None)]

    @pytest.mark.parametrize(
        "user",
        [
            pytest.param(_user(active=False), id="inactive"),
            pytest.param(SimpleNamespace(), id="no-is-active"),
            pytest.param(None, id="none"),
        ],
    )
    def test_a_user_who_is_not_active_holds_nothing(self, manager, user) -> None:
        calls: list[object] = []
        manager.add("blog.view_post", lambda user: calls.append(user) or True)
        assert manager.has_perm(user, "blog.view_post") is False
        assert calls == []

    @pytest.mark.parametrize("answer", [True, False])
    def test_an_active_user_gets_the_answer_of_the_predicate(
        self, manager, answer
    ) -> None:
        asked: list[object] = []
        user = _user()
        manager.add("blog.view_post", lambda who: asked.append(who) or answer)
        assert manager.has_perm(user, "blog.view_post") is answer
        assert asked == [user]

    def test_a_real_anonymous_user_holds_nothing(self, manager) -> None:
        asked: list[object] = []
        manager.add("blog.view_post", lambda who: asked.append(who) or True)
        anonymous = AnonymousUser()
        assert manager.has_perm(anonymous, "blog.view_post") is False
        assert manager.perms_for(anonymous) == frozenset()
        assert manager.has_module_perms(anonymous, "blog") is False
        assert asked == []

    @pytest.mark.usefixtures("object_permission_backend")
    def test_a_real_anonymous_user_is_refused_through_django(self) -> None:
        asked: list[object] = []
        permissions.add_perm("blog.view_post", lambda who: asked.append(who) or True)
        anonymous = AnonymousUser()
        assert anonymous.has_perm("blog.view_post") is False
        assert anonymous.get_all_permissions() == set()
        assert anonymous.has_module_perms("blog") is False
        assert asked == []

    def test_an_unregistered_permission_is_refused(self, manager) -> None:
        assert manager.has_perm(_user(), "blog.change_post") is False


class TestPermsFor:
    """`perms_for` lists every registered permission an active user holds."""

    def test_lists_the_held_ones(self, manager) -> None:
        manager.add("blog.change_post", is_owner)
        manager.add("blog.moderate_post", is_staff)
        manager.add("blog.view_post", lambda: True)
        user = _user(staff=True)
        assert manager.perms_for(user) == frozenset(
            {"blog.moderate_post", "blog.view_post"}
        )
        assert manager.perms_for(user, SimpleNamespace(owner=user)) == frozenset(
            {"blog.change_post", "blog.moderate_post", "blog.view_post"}
        )

    def test_an_inactive_user_holds_nothing(self, manager) -> None:
        manager.add("blog.view_post", lambda: True)
        assert manager.perms_for(_user(active=False)) == frozenset()


class TestHasModulePerms:
    """`has_module_perms` asks every registered permission of the app label."""

    def test_true_when_one_of_the_label_is_held(self, manager) -> None:
        manager.add("blog.change_post", is_owner)
        manager.add("blog.moderate_post", is_staff)
        assert manager.has_module_perms(_user(staff=True), "blog") is True

    def test_false_when_none_of_the_label_is_held(self, manager) -> None:
        manager.add("blog.moderate_post", is_staff)
        manager.add("shop.view_order", lambda: True)
        assert manager.has_module_perms(_user(), "blog") is False

    def test_a_label_prefix_is_not_the_label(self, manager) -> None:
        manager.add("blogroll.view_link", lambda: True)
        assert manager.has_module_perms(_user(), "blog") is False

    def test_the_predicate_receives_no_object(self, manager) -> None:
        seen: list[object] = []
        manager.add(
            "blog.view_post", lambda user, obj: seen.append((user, obj)) or True
        )
        user = _user()
        assert manager.has_module_perms(user, "blog") is True
        assert seen == [(user, None)]

    def test_an_inactive_user_holds_nothing(self, manager) -> None:
        manager.add("blog.view_post", lambda: True)
        assert manager.has_module_perms(_user(active=False), "blog") is False


class TestKnown:
    """`known` accepts what a model, the registry or a neighbour backend grants."""

    @pytest.mark.parametrize(
        "perm", ["auth.add_user", "auth.change_group", "auth.view_permission"]
    )
    def test_a_default_model_permission(self, manager, perm: str) -> None:
        assert manager.known(perm) is True

    def test_a_meta_permissions_entry(self, manager, monkeypatch) -> None:
        monkeypatch.setattr(Group._meta, "permissions", [("audit_group", "Audit")])
        assert manager.known("auth.audit_group") is True

    def test_a_registered_permission(self, manager) -> None:
        manager.add("blog.change_post", is_owner)
        assert manager.known("blog.change_post") is True

    @pytest.mark.parametrize("perm", ["blog.change_post", "auth.fly_user", "nodot"])
    def test_an_unknown_permission(self, manager, perm: str) -> None:
        assert manager.known(perm) is False

    def test_without_the_auth_app_no_model_permission_is_known(
        self, manager, settings
    ) -> None:
        manager.add("blog.change_post", is_owner)
        settings.INSTALLED_APPS = [
            app for app in settings.INSTALLED_APPS if app != "django.contrib.auth"
        ]
        assert manager.known("auth.add_user") is False
        assert manager.known("blog.change_post") is True


class TestKnownNeighbour:
    """A listed compatible backend answers for the permissions it registers."""

    def test_a_listed_neighbour_is_asked(self, manager, rules_module) -> None:
        with override_settings(AUTHENTICATION_BACKENDS=[MODEL_BACKEND, _RULES_BACKEND]):
            assert manager.known("rules.read_book") is True
            assert manager.known("rules.burn_book") is False
        assert rules_module == ["rules.read_book", "rules.burn_book"]

    def test_an_unlisted_neighbour_is_not_asked(self, manager, rules_module) -> None:
        with override_settings(AUTHENTICATION_BACKENDS=[MODEL_BACKEND]):
            assert manager.known("rules.read_book") is False
        assert rules_module == []

    def test_a_model_permission_never_reaches_the_neighbour(
        self, manager, rules_module
    ) -> None:
        with override_settings(AUTHENTICATION_BACKENDS=[MODEL_BACKEND, _RULES_BACKEND]):
            assert manager.known("auth.add_user") is True
        assert rules_module == []

    @pytest.mark.parametrize(
        "installed",
        [
            pytest.param(None, id="module-missing"),
            pytest.param(ModuleType("rules.permissions"), id="callable-missing"),
        ],
    )
    def test_a_neighbour_that_fails_to_import_is_skipped(
        self, manager, monkeypatch, installed: ModuleType | None
    ) -> None:
        monkeypatch.setitem(sys.modules, "rules.permissions", installed)
        with override_settings(AUTHENTICATION_BACKENDS=[MODEL_BACKEND, _RULES_BACKEND]):
            assert manager.known("rules.read_book") is False


@pytest.fixture()
def project_backend(rules_module, monkeypatch: pytest.MonkeyPatch) -> str:
    """Install a rules backend class and a project subclass of it, named by path."""
    rules_backend = type("ObjectPermissionBackend", (), {})
    rules_backend.__module__ = "rules.permissions"
    sys.modules["rules.permissions"].ObjectPermissionBackend = rules_backend
    project = ModuleType("project_auth")
    project.ProjectBackend = type("ProjectBackend", (rules_backend,), {})
    project.ProjectBackend.__module__ = "project_auth"
    monkeypatch.setitem(sys.modules, "project_auth", project)
    return "project_auth.ProjectBackend"


@pytest.fixture()
def imported(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every dotted path the manager imports."""
    paths: list[str] = []

    def spy(path: str) -> object:
        paths.append(path)
        return import_string(path)

    monkeypatch.setattr(manager_module, "import_string", spy)
    return paths


class TestNeighbourMatching:
    """A neighbour counts when a listed backend is it or inherits from it."""

    def test_a_subclass_of_the_neighbour_is_recognised(
        self, manager, project_backend: str, rules_module
    ) -> None:
        with override_settings(
            AUTHENTICATION_BACKENDS=[MODEL_BACKEND, project_backend]
        ):
            assert manager.known("rules.read_book") is True
        assert rules_module == ["rules.read_book"]

    def test_an_unrelated_listed_class_is_not_the_neighbour(
        self, manager, rules_module, imported: list[str]
    ) -> None:
        with override_settings(AUTHENTICATION_BACKENDS=[MODEL_BACKEND]):
            assert manager.known("rules.read_book") is False
        assert imported == [MODEL_BACKEND]
        assert rules_module == []

    def test_an_unlisted_neighbour_is_never_imported(
        self, manager, monkeypatch, imported: list[str]
    ) -> None:
        monkeypatch.setitem(sys.modules, "rules.permissions", None)
        with override_settings(AUTHENTICATION_BACKENDS=[MODEL_BACKEND]):
            assert manager.known("rules.read_book") is False
        assert "rules.permissions.perm_exists" not in imported

    def test_a_listed_path_that_fails_to_import_still_matches_by_name(
        self, manager, rules_module
    ) -> None:
        with pytest.raises(ImportError):
            import_string(_RULES_BACKEND)
        with override_settings(AUTHENTICATION_BACKENDS=[_RULES_BACKEND]):
            assert manager.known("rules.read_book") is True

    @pytest.mark.parametrize(
        "entry",
        [
            pytest.param("os.path.join", id="a-function"),
            pytest.param("nowhere.Backend", id="unimportable"),
        ],
    )
    def test_an_entry_that_is_no_class_inherits_nothing(
        self, manager, rules_module, entry: str
    ) -> None:
        with override_settings(AUTHENTICATION_BACKENDS=[MODEL_BACKEND, entry]):
            assert manager.known("rules.read_book") is False
        assert rules_module == []


class TestModelPermissionMemo:
    """The model permissions are kept while the app registry hands out one list."""

    def test_reused_on_the_same_model_list(self, manager) -> None:
        first = manager._model_permissions()
        assert manager._model_permissions() is first

    def test_rebuilt_when_the_app_registry_changes(self, manager, monkeypatch) -> None:
        first = manager._model_permissions()
        monkeypatch.setattr(Group._meta, "permissions", [("audit_group", "Audit")])
        apps.clear_cache()
        rebuilt = manager._model_permissions()
        assert rebuilt is not first
        assert rebuilt == first | {"auth.audit_group"}

    def test_reset_drops_the_memo_and_the_registry(self, manager, monkeypatch) -> None:
        manager.add("blog.change_post", is_owner)
        manager.known("auth.add_user")
        monkeypatch.setattr(Group._meta, "permissions", [("audit_group", "Audit")])
        manager.reset()
        assert manager.known("auth.audit_group") is True
        assert manager.known("blog.change_post") is False
        assert len(manager.registry) == 0


class TestModuleFunctions:
    """The module functions answer through the shared manager."""

    def test_add_and_has_perm(self) -> None:
        assert permissions.add_perm("blog.change_post", is_owner) is None
        user = _user()
        post = SimpleNamespace(owner=user)
        held = permission_manager.registry.get("blog.change_post")
        assert held is not None
        assert held.test is is_owner
        assert permissions.has_perm(user, "blog.change_post", post) is True
        assert permissions.has_perm(user, "blog.change_post") is False

    def test_add_perm_keeps_a_built_predicate(self) -> None:
        rule = predicate(is_staff)
        permissions.add_perm("blog.change_post", rule)
        assert permission_manager.registry.get("blog.change_post") is rule
        assert permissions.has_perm(_user(staff=True), "blog.change_post") is True
