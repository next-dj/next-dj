from collections.abc import Callable, Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from django import forms as django_forms
from django.contrib.auth.models import Group, Permission
from django.core.checks import CheckMessage, Error

from next.components.context import component
from next.deps import DObject
from next.deps.checks import (
    check_object_component_contexts,
    check_object_declarations,
    check_object_form_parents,
    check_object_hints,
    check_object_managers,
    check_object_page_bindings,
    check_object_scope,
    object_sites,
)
from next.forms import ActionRegistration, Form, FormWizard
from next.forms.manager import form_action_manager
from next.introspect import describe_callable
from tests.support import (
    bound_dependency,
    check_ids,
    installed_apps,
    isolated_form_registries,
    next_framework_settings_stand_in,
    routed,
    write_app,
    write_page,
    write_urlconf,
)
from tests.support.access import write_proxy


HEADER = """
from django.contrib.auth.models import Group, Permission

from next.deps import DObject
from next.pages import context


template = "x"
"""

PARENT = "permissions"
GROUP_ROUTE = "[int:group]"
CHAIN_ROUTE = "[int:permission]/[int:group]"

GROUP_CONTEXT = """
@context("roster")
def roster(group: DObject[Group]) -> Group:
    return group
"""

PERMISSION_ANCESTOR = """
@context("permissions", inherit_context=True)
def permissions(permission: DObject[Permission]) -> Permission:
    return permission
"""

PERMISSION_LOCAL = """
@context("permissions")
def permissions(permission: DObject[Permission]) -> Permission:
    return permission
"""

ROSTER_CHILD = """
@context("roster")
def roster(group: DObject[Group, "permissions"]) -> Group:
    return group
"""

COMPONENT_SOURCE = """
from django.contrib.auth.models import Group

from next import component
from next.deps import DObject


@component.context("card")
def card(group: DObject[Group]) -> str:
    return group.name
"""

COMPONENT_BACKEND = {
    "BACKEND": "next.components.FileComponentsBackend",
    "DIRS": [],
    "COMPONENTS_DIR": "_components",
}

CheckFunction = Callable[[], list[CheckMessage]]

MOUNTED_WALLETS = pytest.mark.parametrize(
    ("entry", "route"),
    [
        ('path("w/<int:wallet>/", pages("wallets"))', "/w/<int:wallet>/ops/"),
        ('path("t/<int:wallet>/", include("next.urls"))', "/t/<int:wallet>/ops/"),
    ],
    ids=["pages_include", "framework_include"],
)


def _run(
    root: Path, check: CheckFunction, *pages: tuple[str, str], **framework: object
) -> list:
    for trail, body in pages:
        write_page(root, trail, HEADER + body)
    with routed(root, **framework):
        return check()


def _run_mounted(root: Path, param: str, entry: str) -> list:
    write_app(root, "wallets", {"pages": ()})
    write_page(
        root / "wallets" / "pages",
        "ops",
        HEADER
        + f'\n@context("row")\ndef row({param}: DObject[Group]) -> Group:\n'
        + f"    return {param}\n",
    )
    urlconf = write_urlconf(root, "wallet_urls", entry)
    with installed_apps(root, "wallets", urlconf=urlconf):
        return check_object_page_bindings()


def _rows(group: DObject[Group]) -> Group:
    return group


def _member(group: DObject[Group, PARENT]) -> Group:
    return group


def _unresolved(group) -> None:
    return None


_unresolved.__annotations__ = {"group": "DObject[Missing]"}


def _opaque(group) -> None:
    return None


_opaque.__annotations__ = {"group": "DObject[Missing]"}
_opaque.__signature__ = "no signature"


def _registered(
    *,
    handler: Callable[..., object] | None = None,
    form_class: object = None,
    name: str = "object_check_action",
) -> None:
    form_action_manager.default_backend.register_action(
        ActionRegistration(
            name=name,
            file_path=__file__,
            scope="shared",
            handler=handler,
            form_class=form_class,
        )
    )


@pytest.fixture()
def forms_isolated() -> Generator[None, None, None]:
    with isolated_form_registries():
        yield


@pytest.fixture()
def component_registrations() -> Generator[None, None, None]:
    before = set(component._registry._registry)
    yield
    for path in set(component._registry._registry) - before:
        component._registry.unregister(path)


class TestFailedRouters:
    """Every `DObject` check stays silent while the routers fail to load."""

    @pytest.mark.parametrize(
        "check",
        [
            check_object_component_contexts,
            check_object_form_parents,
            check_object_hints,
            check_object_managers,
            check_object_page_bindings,
            check_object_scope,
        ],
    )
    def test_no_router_manager_draws_no_message(self, check: CheckFunction) -> None:
        failure = Error("broken", id="next.E007")
        with patch(
            "next.deps.checks.get_router_manager", return_value=(None, [failure])
        ):
            assert object_sites() is None
            assert check() == []


class TestManagers:
    """A `DObject` model needs `visible_to` on its default manager (`next.E153`)."""

    def test_a_missing_hook_is_reported_once_per_model(self, tmp_path: Path) -> None:
        twice = GROUP_CONTEXT + (
            '\n@context("again")\ndef again(group: DObject[Group]) -> Group:\n'
            "    return group\n"
        )
        messages = _run(tmp_path / "pages", check_object_managers, (GROUP_ROUTE, twice))
        assert check_ids(messages) == ["next.E153"]
        assert messages[0].obj is Group
        assert 'DObject[auth.Group] on parameter "group" of ' in messages[0].msg
        assert "auth.Group._default_manager.visible_to(scope)" in messages[0].msg

    def test_a_parent_marker_names_its_parent(self, tmp_path: Path) -> None:
        messages = _run(
            tmp_path / "pages",
            check_object_managers,
            ("[int:permission]", PERMISSION_ANCESTOR),
            (CHAIN_ROUTE, ROSTER_CHILD),
        )
        assert check_ids(messages) == ["next.E153", "next.E153"]
        assert 'DObject[auth.Group, "permissions"]' in messages[1].msg

    def test_a_hooked_manager_is_silent(self, tmp_path: Path, scoped_models) -> None:
        assert (
            _run(
                tmp_path / "pages", check_object_managers, (GROUP_ROUTE, GROUP_CONTEXT)
            )
            == []
        )

    def test_an_uncallable_hook_counts_as_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Group._default_manager, "visible_to", "rows", raising=False)
        messages = _run(
            tmp_path / "pages", check_object_managers, (GROUP_ROUTE, GROUP_CONTEXT)
        )
        assert check_ids(messages) == ["next.E153"]

    def test_a_named_dependency_is_read_too(self, tmp_path: Path) -> None:
        def owner(permission: DObject[Permission]) -> Permission:
            return permission

        with bound_dependency("owner", owner):
            messages = _run(tmp_path / "pages", check_object_managers)
        assert check_ids(messages) == ["next.E153"]
        assert messages[0].obj is Permission

    def test_a_returned_marker_and_a_broken_hint_are_skipped(
        self, tmp_path: Path
    ) -> None:
        body = (
            '\n@context("made")\ndef made() -> DObject[Group]:\n    return None\n'
            '\n@context("broken")\ndef broken(group: "Missing") -> str:\n'
            '    return ""\n'
        )
        assert _run(tmp_path / "pages", check_object_managers, ("", body)) == []


class TestRouteKwargs:
    """A page `DObject` names a kwarg its route captures (`next.W126`)."""

    def test_a_captured_kwarg_is_silent(self, tmp_path: Path) -> None:
        messages = _run(
            tmp_path / "pages", check_object_page_bindings, (GROUP_ROUTE, GROUP_CONTEXT)
        )
        assert messages == []

    @pytest.mark.parametrize(
        ("trail", "captured"),
        [("", "captures none"), ("[int:team]", "captures team")],
        ids=["root", "other_kwarg"],
    )
    def test_an_uncaptured_kwarg_warns(
        self, tmp_path: Path, trail: str, captured: str
    ) -> None:
        messages = _run(
            tmp_path / "pages", check_object_page_bindings, (trail, GROUP_CONTEXT)
        )
        assert check_ids(messages) == ["next.W126"]
        assert captured in messages[0].msg
        assert 'by the URL kwarg "group"' in messages[0].msg
        assert messages[0].obj == str(tmp_path / "pages" / trail / "page.py")

    def test_a_refused_route_draws_no_warning(self, tmp_path: Path) -> None:
        messages = _run(
            tmp_path / "pages",
            check_object_page_bindings,
            ("[int:team]/[int:team]", GROUP_CONTEXT),
        )
        assert messages == []

    @MOUNTED_WALLETS
    def test_a_kwarg_the_include_captures_is_silent(
        self, tmp_path: Path, entry: str, route: str
    ) -> None:
        assert _run_mounted(tmp_path, "wallet", entry) == []

    @MOUNTED_WALLETS
    def test_a_kwarg_no_part_of_the_route_captures_warns(
        self, tmp_path: Path, entry: str, route: str
    ) -> None:
        messages = _run_mounted(tmp_path, "account", entry)
        assert check_ids(messages) == ["next.W126"]
        assert f'the route "{route}" of ' in messages[0].msg
        assert "captures wallet." in messages[0].msg

    def test_a_callable_under_two_keys_warns_once(self, tmp_path: Path) -> None:
        body = (
            '\n@context("first")\n@context("second")\n'
            "def both(group: DObject[Group]) -> Group:\n    return group\n"
        )
        messages = _run(tmp_path / "pages", check_object_page_bindings, ("", body))
        assert check_ids(messages) == ["next.W126"]


class TestParents:
    """A page parent comes from a context running before the marker (`next.E154`)."""

    def test_an_inherited_ancestor_value_binds_the_parent(self, tmp_path: Path) -> None:
        messages = _run(
            tmp_path / "pages",
            check_object_page_bindings,
            ("[int:permission]", PERMISSION_ANCESTOR),
            (CHAIN_ROUTE, ROSTER_CHILD),
        )
        assert messages == []

    def test_an_uninherited_ancestor_value_misses(self, tmp_path: Path) -> None:
        messages = _run(
            tmp_path / "pages",
            check_object_page_bindings,
            ("[int:permission]", PERMISSION_LOCAL),
            (CHAIN_ROUTE, ROSTER_CHILD),
        )
        assert check_ids(messages) == ["next.E154"]
        assert '"permissions"' in messages[0].msg
        assert '"/<int:permission>/<int:group>/" route' in messages[0].msg
        assert '@context("permissions", inherit_context=True)' in messages[0].msg
        assert messages[0].obj == str(tmp_path / "pages" / CHAIN_ROUTE / "page.py")

    @pytest.mark.parametrize(
        ("child_key", "expected"),
        [("roster", []), ("group", ["next.E154"])],
        ids=["parent_runs_first", "child_runs_first"],
    )
    def test_the_merge_order_of_the_page_decides(
        self, tmp_path: Path, child_key: str, expected: list[str]
    ) -> None:
        child = ROSTER_CHILD.replace('"roster"', f'"{child_key}"')
        messages = _run(
            tmp_path / "pages",
            check_object_page_bindings,
            (CHAIN_ROUTE, PERMISSION_LOCAL + child),
        )
        assert check_ids(messages) == expected

    @pytest.mark.parametrize(
        ("decoration", "expected"),
        [('"roster"', []), ('"roster", inherit_context=True', ["next.E154"])],
        ids=["keyed_after_keyless", "inherited_before_keyless"],
    )
    def test_a_keyless_context_leaves_later_parents_unjudged(
        self, tmp_path: Path, decoration: str, expected: list[str]
    ) -> None:
        child = ROSTER_CHILD.replace('"roster"', decoration)
        keyless = "\n@context\ndef extra() -> dict:\n    return {}\n"
        messages = _run(
            tmp_path / "pages",
            check_object_page_bindings,
            (CHAIN_ROUTE, keyless + child),
        )
        assert check_ids(messages) == expected

    @pytest.mark.parametrize(
        ("parent_zone", "child_zone", "expected"),
        [
            (None, "side", []),
            ("side", "side", []),
            ("side", None, ["next.E154"]),
            ("side", "main", ["next.E154"]),
        ],
        ids=["unbound_parent", "same_zone", "unbound_child", "other_zone"],
    )
    def test_a_zone_bound_parent_binds_its_own_zone_only(
        self,
        tmp_path: Path,
        parent_zone: str | None,
        child_zone: str | None,
        expected: list[str],
    ) -> None:
        parent = PERMISSION_LOCAL.replace(
            '"permissions"', f'"permissions", zone={parent_zone!r}'
        )
        child = ROSTER_CHILD.replace('"roster"', f'"roster", zone={child_zone!r}')
        messages = _run(
            tmp_path / "pages",
            check_object_page_bindings,
            (CHAIN_ROUTE, parent + child),
        )
        assert check_ids(messages) == expected


class TestFormParents:
    """A form handler or hook declares no parent `DObject` (`next.E155`)."""

    def test_a_handler_parent_is_reported(self, tmp_path: Path, forms_isolated) -> None:
        _registered(handler=_member)
        messages = _run(tmp_path / "pages", check_object_form_parents)
        assert check_ids(messages) == ["next.E155"]
        assert messages[0].obj == describe_callable(_member)
        assert "a form handler or hook resolves with no page context" in (
            messages[0].msg
        )

    def test_a_handler_under_two_actions_is_reported_once(
        self, tmp_path: Path, forms_isolated
    ) -> None:
        _registered(handler=_member, name="first_member_action")
        _registered(handler=_member, name="second_member_action")
        messages = _run(tmp_path / "pages", check_object_form_parents)
        assert check_ids(messages) == ["next.E155"]

    def test_a_parentless_handler_is_silent(
        self, tmp_path: Path, forms_isolated
    ) -> None:
        _registered(handler=_rows)
        assert _run(tmp_path / "pages", check_object_form_parents) == []

    def test_a_form_hook_parent_is_reported(
        self, tmp_path: Path, forms_isolated
    ) -> None:
        class MemberForm(Form):
            @classmethod
            def get_initial(cls, group: DObject[Group, PARENT]) -> dict:
                return {}

        messages = _run(tmp_path / "pages", check_object_form_parents)
        assert check_ids(messages) == ["next.E155"]
        assert messages[0].obj == describe_callable(MemberForm.get_initial)

    def test_an_object_permission_parent_is_reported(
        self, tmp_path: Path, forms_isolated
    ) -> None:
        class GuardedForm(Form):
            def has_object_permission(self, group: DObject[Group, PARENT]) -> bool:
                return True

        messages = _run(tmp_path / "pages", check_object_form_parents)
        assert check_ids(messages) == ["next.E155"]
        assert messages[0].obj == describe_callable(GuardedForm.has_object_permission)

    @pytest.mark.parametrize("site", ["check_permissions", "done", "step"])
    def test_a_wizard_parent_is_reported(
        self, tmp_path: Path, forms_isolated, site: str
    ) -> None:
        class StepForm(django_forms.Form):
            def has_object_permission(self, group: DObject[Group, PARENT]) -> bool:
                return True

        class PlainStep(django_forms.Form):
            pass

        def check_permissions(cls, group: DObject[Group, PARENT]) -> bool:
            return True

        def done(self, group: DObject[Group, PARENT]) -> None:
            return None

        hooks = {
            "check_permissions": {"check_permissions": classmethod(check_permissions)},
            "done": {"done": done},
            "step": {},
        }[site]
        step = StepForm if site == "step" else PlainStep
        meta = type("Meta", (), {"steps": [("only", step)]})
        wizard = type("MemberWizard", (FormWizard,), {"Meta": meta, **hooks})
        expected = {
            "check_permissions": wizard.check_permissions,
            "done": wizard.done,
            "step": StepForm.has_object_permission,
        }[site]
        messages = _run(tmp_path / "pages", check_object_form_parents)
        assert check_ids(messages) == ["next.E155"]
        assert messages[0].obj == describe_callable(expected)

    def test_a_form_factory_parent_is_reported(
        self, tmp_path: Path, forms_isolated
    ) -> None:
        def factory(group: DObject[Group, PARENT]) -> type:
            return Form

        _registered(handler=_rows, form_class=factory)
        messages = _run(tmp_path / "pages", check_object_form_parents)
        assert check_ids(messages) == ["next.E155"]
        assert messages[0].obj == describe_callable(factory)

    def test_a_plain_form_without_hooks_is_silent(
        self, tmp_path: Path, forms_isolated
    ) -> None:
        class PlainForm(django_forms.Form):
            pass

        _registered(form_class=PlainForm)
        assert _run(tmp_path / "pages", check_object_form_parents) == []


class TestComponentContexts:
    """A `@component.context` callable declares no `DObject` (`next.E156`)."""

    @pytest.mark.parametrize("location", ["page_tree", "project_root"])
    def test_a_component_marker_is_reported(
        self, tmp_path: Path, component_registrations, location: str
    ) -> None:
        roots = tmp_path / "components"
        backend = {**COMPONENT_BACKEND, "DIRS": [str(roots)]}
        folder = (
            tmp_path / "pages" / "_components" / "card"
            if location == "page_tree"
            else roots / "card"
        )
        folder.mkdir(parents=True)
        (folder / "component.py").write_text(COMPONENT_SOURCE)
        (folder / "component.djx").write_text("{{ card }}")
        messages = _run(
            tmp_path / "pages",
            check_object_component_contexts,
            ("", ""),
            COMPONENT_BACKENDS=[backend],
        )
        assert check_ids(messages) == ["next.E156"]
        assert "a component context never receives" in messages[0].msg

    def test_a_callable_under_two_keys_is_reported_once(
        self, tmp_path: Path, component_registrations
    ) -> None:
        source = tmp_path / "components" / "card" / "component.py"
        component._registry.register(source, "first", _rows)
        component._registry.register(source, "second", _rows)
        backend = {**COMPONENT_BACKEND, "DIRS": [str(tmp_path / "components")]}
        messages = _run(
            tmp_path / "pages",
            check_object_component_contexts,
            ("", ""),
            COMPONENT_BACKENDS=[backend],
        )
        assert check_ids(messages) == ["next.E156"]

    @pytest.mark.parametrize(
        "configs", [[], COMPONENT_BACKEND], ids=["empty", "not_a_list"]
    )
    def test_no_component_backend_reads_no_component(
        self, tmp_path: Path, component_registrations, configs: object
    ) -> None:
        component._registry.register(tmp_path / "component.py", "card", _rows)
        stand_in = next_framework_settings_stand_in(COMPONENT_BACKENDS=configs)
        with patch("next.deps.checks.next_framework_settings", stand_in):
            assert _run(tmp_path / "pages", check_object_component_contexts) == []


class TestHints:
    """A `DObject` sits on a callable whose hints resolve (`next.E158`)."""

    def test_an_unresolved_page_hint_is_reported(self, tmp_path: Path) -> None:
        body = (
            '\n@context("roster")\ndef roster(group: "DObject[Missing]") -> str:\n'
            '    return ""\n'
        )
        messages = _run(tmp_path / "pages", check_object_hints, ("", body))
        assert check_ids(messages) == ["next.E158"]
        assert 'Parameter "group" of "roster"' in messages[0].msg

    def test_an_unresolved_dependency_hint_is_reported(self, tmp_path: Path) -> None:
        with bound_dependency("later", _unresolved):
            messages = _run(tmp_path / "pages", check_object_hints)
        assert check_ids(messages) == ["next.E158"]
        assert messages[0].obj == describe_callable(_unresolved)

    @pytest.mark.parametrize(
        "annotation",
        ['"Missing"', '"MyDObjectish[Missing]"', "DObject[Group]"],
        ids=["other_name", "longer_name", "resolved"],
    )
    def test_any_other_hint_is_silent(self, tmp_path: Path, annotation: str) -> None:
        body = (
            f'\n@context("roster")\ndef roster(group: {annotation}) -> str:\n'
            '    return ""\n'
        )
        assert _run(tmp_path / "pages", check_object_hints, ("", body)) == []

    def test_an_uninspectable_callable_is_skipped(self, tmp_path: Path) -> None:
        with bound_dependency("opaque", _opaque):
            assert _run(tmp_path / "pages", check_object_hints) == []


class TestScope:
    """A project with a `DObject` registers the `scope` dependency (`next.E157`)."""

    def test_a_marker_without_scope_is_reported(self, tmp_path: Path) -> None:
        messages = _run(
            tmp_path / "pages", check_object_scope, (GROUP_ROUTE, GROUP_CONTEXT)
        )
        assert check_ids(messages) == ["next.E157"]
        assert 'DObject[auth.Group] on parameter "group"' in messages[0].msg
        assert '@resolver.dependency("scope")' in messages[0].msg

    def test_a_registered_scope_is_silent(self, tmp_path: Path) -> None:
        with bound_dependency("scope", lambda: None):
            messages = _run(
                tmp_path / "pages", check_object_scope, (GROUP_ROUTE, GROUP_CONTEXT)
            )
        assert messages == []

    def test_a_project_without_markers_needs_no_scope(self, tmp_path: Path) -> None:
        assert _run(tmp_path / "pages", check_object_scope, ("", "")) == []


METADATA_GROUP = """
from next.pages import page


@page.metadata
def meta(group: DObject[Group]) -> dict:
    return {"title": group.name}
"""

METADATA_MEMBER = """
from next.pages import page


@page.metadata
def meta(group: DObject[Group, "permissions"]) -> dict:
    return {"title": group.name}
"""

INHERITED_METADATA = """
from next.pages import page


@page.metadata(inherit=True)
def meta(group: DObject[Group]) -> dict:
    return {"title": group.name}
"""


class TestMetadataCallables:
    """A `DObject` on `@page.metadata` answers the same checks a context does."""

    @pytest.mark.parametrize(
        ("trail", "expected"),
        [(GROUP_ROUTE, []), ("", ["next.W126"])],
        ids=["captured", "uncaptured"],
    )
    def test_route_kwargs(self, tmp_path: Path, trail: str, expected) -> None:
        messages = _run(
            tmp_path / "pages", check_object_page_bindings, (trail, METADATA_GROUP)
        )
        assert check_ids(messages) == expected

    @pytest.mark.parametrize(
        ("before", "expected"),
        [
            pytest.param("", ["next.E154"], id="no-parent"),
            pytest.param(PERMISSION_LOCAL, [], id="parent-context"),
            pytest.param(
                PERMISSION_LOCAL.replace('"permissions"', '"permissions", zone="side"'),
                ["next.E154"],
                id="zone-bound-parent",
            ),
            pytest.param(
                "\n@context\ndef extra() -> dict:\n    return {}\n",
                [],
                id="after-keyless",
            ),
        ],
    )
    def test_parents(self, tmp_path: Path, before: str, expected) -> None:
        messages = _run(
            tmp_path / "pages",
            check_object_page_bindings,
            (CHAIN_ROUTE, before + METADATA_MEMBER),
        )
        assert check_ids(messages) == expected

    def test_an_inherited_ancestor_context_binds_the_parent(
        self, tmp_path: Path
    ) -> None:
        messages = _run(
            tmp_path / "pages",
            check_object_page_bindings,
            ("[int:permission]", PERMISSION_ANCESTOR),
            (CHAIN_ROUTE, METADATA_MEMBER),
        )
        assert messages == []

    def test_an_inherited_callable_is_judged_on_every_route(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "pages"
        messages = _run(
            root,
            check_object_page_bindings,
            ("[int:permission]", INHERITED_METADATA),
            (CHAIN_ROUTE, ""),
        )
        assert check_ids(messages) == ["next.W126"]
        assert messages[0].obj == str(root / "[int:permission]" / "page.py")

    def test_a_refused_chain_is_not_judged(self, tmp_path: Path) -> None:
        body = METADATA_GROUP + '\nmetadata = {"title": "x"}\n'
        messages = _run(tmp_path / "pages", check_object_page_bindings, ("", body))
        assert messages == []

    @pytest.mark.parametrize(
        ("check", "expected"),
        [
            pytest.param(check_object_managers, ["next.E153"], id="managers"),
            pytest.param(check_object_scope, ["next.E157"], id="scope"),
        ],
    )
    def test_the_project_checks(self, tmp_path: Path, check, expected) -> None:
        messages = _run(tmp_path / "pages", check, (GROUP_ROUTE, METADATA_GROUP))
        assert check_ids(messages) == expected

    def test_an_unresolved_hint(self, tmp_path: Path) -> None:
        body = (
            "\nfrom next.pages import page\n\n\n@page.metadata\n"
            'def meta(group: "DObject[Missing]") -> dict:\n    return {}\n'
        )
        messages = _run(tmp_path / "pages", check_object_hints, ("", body))
        assert check_ids(messages) == ["next.E158"]

    def test_a_page_that_failed_to_load_is_not_judged(self, tmp_path: Path) -> None:
        broken = METADATA_GROUP + '\nraise RuntimeError("broken page")\n'
        messages = _run(
            tmp_path / "pages",
            check_object_page_bindings,
            ("", broken),
            ("[int:team]", METADATA_GROUP),
        )
        assert check_ids(messages) == ["next.W126"]
        assert messages[0].obj == str(tmp_path / "pages" / "[int:team]" / "page.py")


DECLARES_GROUP = """
from next.pages import page

page.permission_required("auth.view_group", obj=DObject[Group])
"""


class TestObjectDeclarations:
    """An `obj=DObject[...]` reads the URL kwarg named after its model (`next.E162`)."""

    @pytest.mark.parametrize(
        ("trail", "expected"),
        [(GROUP_ROUTE, []), ("[int:team]", ["next.E162"])],
        ids=["captured", "uncaptured"],
    )
    def test_the_page_route(self, tmp_path: Path, trail: str, expected) -> None:
        messages = _run(
            tmp_path / "pages", check_object_declarations, (trail, DECLARES_GROUP)
        )
        assert check_ids(messages) == expected

    def test_a_proxy_is_judged_on_every_page_below_it(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        declaring = write_proxy(
            root,
            "from django.contrib.auth.models import Group\n\n"
            "from next.deps import DObject\n\n"
            'proxy.permission_required("auth.view_group", obj=DObject[Group])\n',
        )
        messages = _run(
            root, check_object_declarations, (GROUP_ROUTE, ""), ("[int:team]", "")
        )
        assert check_ids(messages) == ["next.E162"]
        assert messages[0].obj == str(declaring)
        assert (
            "permission_required('auth.view_group', obj=DObject[auth.Group]) in "
            f'{declaring} loads its row by the URL kwarg "group"'
        ) in messages[0].msg
        assert 'route "/<int:team>/"' in messages[0].msg
        assert "[int:group]" in messages[0].hint

    def test_a_lifted_page_is_not_judged(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        write_proxy(
            root,
            "from django.contrib.auth.models import Group\n\n"
            "from next.deps import DObject\n\n"
            'proxy.permission_required("auth.view_group", obj=DObject[Group])\n',
        )
        messages = _run(
            root,
            check_object_declarations,
            ("[int:team]", "\nfrom next.pages import page\n\npage.public()\n"),
        )
        assert messages == []

    @pytest.mark.parametrize(
        ("check", "expected"),
        [
            pytest.param(check_object_managers, ["next.E153"], id="managers"),
            pytest.param(check_object_scope, ["next.E157"], id="scope"),
        ],
    )
    def test_the_project_checks(self, tmp_path: Path, check, expected) -> None:
        messages = _run(tmp_path / "pages", check, (GROUP_ROUTE, DECLARES_GROUP))
        assert check_ids(messages) == expected
        assert (
            "permission_required('auth.view_group', obj=DObject[auth.Group]) in "
        ) in messages[0].msg

    def test_a_declaration_on_two_pages_is_judged_once(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        write_proxy(
            root,
            "from django.contrib.auth.models import Group\n\n"
            "from next.deps import DObject\n\n"
            'proxy.permission_required("auth.view_group", obj=DObject[Group])\n',
        )
        messages = _run(
            root, check_object_managers, (GROUP_ROUTE, ""), (f"{GROUP_ROUTE}/more", "")
        )
        assert check_ids(messages) == ["next.E153"]
