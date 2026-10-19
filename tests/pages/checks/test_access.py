from pathlib import Path

import pytest
from django.test import Client

from next.forms.checks.actions import (
    check_component_action_objects,
    check_public_actions,
)
from next.pages.checks.access import (
    check_declaring_modules,
    check_guards_over_pages,
    check_public_beside_requirements,
)
from next.pages.checks.modules import check_page_module_imports
from next.pages.loaders import load_python_module_memo
from next.permissions.access import declared_files
from tests.support import routed, write_page
from tests.support.access import (
    COMPONENTS,
    login_required_middleware,
    page_source,
    write_guard,
)


_CARD = (
    "from next.components import component\n\n"
    'component.permission_required("auth.view_group")\n'
)


def _ids(messages) -> list[tuple[str, str]]:
    return [(message.id, Path(message.obj).name) for message in messages]


class TestPublicBesideRequirements:
    """next.E163 flags a `public()` beside a requirement of the same file."""

    @pytest.mark.parametrize(
        ("guard", "page", "expected"),
        [
            ("", "page.public()\n", []),
            ("", "page.public()\npage.login_required()\n", [("next.E163", "page.py")]),
            (
                (
                    "page.public(inherit=True)\n"
                    'page.permission_required("auth.view_group", inherit=True)\n'
                ),
                "",
                [("next.E163", "page.py")],
            ),
            (
                "page.public()\npage.login_required(inherit=True)\n",
                "",
                [("next.E163", "page.py")],
            ),
            (
                (
                    "page.public(inherit=True)\n"
                    'page.permission_required("auth.view_group")\n'
                ),
                "",
                [("next.E163", "page.py")],
            ),
            ("page.login_required(inherit=True)\n", "page.public()\n", []),
            ("page.login_required(inherit=True)\n", "page.public(inherit=True)\n", []),
        ],
        ids=[
            "public",
            "page",
            "inherited",
            "mixed",
            "mixed-lift",
            "lift",
            "lift-below",
        ],
    )
    def test_one_file_with_both(self, root, guard, page, expected) -> None:
        write_guard(root, guard)
        write_page(root, "a", page_source(page))

        with routed(root):
            assert _ids(check_public_beside_requirements()) == expected


class TestDeclaringModules:
    """next.E164 flags a declaration bound to a file no endpoint reads."""

    def test_a_declaration_through_a_helper(self, root) -> None:
        write_page(
            root,
            "a",
            page_source(
                "from tests.support.access import declare_login\n\ndeclare_login()\n"
            ),
        )

        with routed(root):
            [error] = check_declaring_modules()

        assert (error.id, Path(error.obj).name) == ("next.E164", "access.py")
        assert "helper" in error.hint

    @pytest.mark.parametrize(
        ("guard", "page"),
        [
            ("page.login_required(inherit=True)\n", "page.public()\n"),
            ("", "page.login_required()\n"),
        ],
        ids=["guard-and-page", "page"],
    )
    def test_segment_modules_are_silent(self, root, guard, page) -> None:
        write_guard(root, guard)
        write_page(root, "a", page_source(page))

        with routed(root):
            assert check_declaring_modules() == []

    def test_a_page_module_outside_a_tree_is_flagged(self, root, tmp_path) -> None:
        stray = write_page(
            tmp_path / "stray", "", page_source("page.login_required()\n")
        )
        load_python_module_memo(stray)

        with routed(root):
            [error] = check_declaring_modules()

        assert error.obj == str(stray)

    def test_a_component_module_is_silent(self, root) -> None:
        card = root / "_components" / "card"
        card.mkdir(parents=True)
        (card / "component.py").write_text(_CARD)
        (card / "component.djx").write_text("card")
        write_page(root, "a", page_source(template='{% component "card" %}'))

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            Client().get("/a/")
            assert declared_files() == (card / "component.py",)
            assert check_declaring_modules() == []


class TestGuardsOverPages:
    """next.W128 warns about a `page.py` that guards the pages below and has none."""

    def test_a_guard_over_a_page_is_silent(self, root) -> None:
        write_guard(root / "a", "page.login_required(inherit=True)\n")
        write_page(root, "a/b", page_source())

        with routed(root):
            assert check_guards_over_pages() == []

    @pytest.mark.parametrize("trail", ["a", "empty/_drafts"], ids=["beside", "private"])
    def test_a_guard_over_no_page_warns(self, root, trail) -> None:
        write_guard(root / "empty", "page.login_required(inherit=True)\n")
        write_page(root, trail, page_source())

        with routed(root):
            assert _ids(check_guards_over_pages()) == [("next.W128", "page.py")]

    def test_a_guard_over_another_guard_alone_warns_for_both(self, root) -> None:
        write_guard(root / "a", "page.login_required(inherit=True)\n")
        write_guard(root / "a" / "b", "page.public(inherit=True)\n")
        write_page(root, "c", page_source())

        with routed(root):
            assert _ids(check_guards_over_pages()) == [
                ("next.W128", "page.py"),
                ("next.W128", "page.py"),
            ]

    def test_a_page_with_a_body_is_no_guard(self, root) -> None:
        write_page(root, "a", page_source("page.login_required(inherit=True)\n"))

        with routed(root):
            assert check_guards_over_pages() == []


def _open_actions(declared: str = "", guard: str = "") -> str:
    return (
        "from next.forms import action\n"
        f"{page_source(declared)}\n"
        '@action("first_open")\n'
        "def first_open():\n"
        "    return None\n\n"
        f'@action("second_open"{guard})\n'
        "def second_open():\n"
        "    return None\n"
    )


@pytest.mark.usefixtures("form_registries")
class TestPublicActions:
    """next.W129 warns once per file about the actions nothing guards."""

    @pytest.fixture(autouse=True)
    def _guard_elsewhere(self, root) -> None:
        write_guard(root / "elsewhere", "page.login_required(inherit=True)\n")

    _actions = staticmethod(_open_actions)

    def test_open_actions_warn_once_per_file(self, root) -> None:
        page_path = write_page(root, "a", self._actions())

        with routed(root):
            [warning] = check_public_actions()

        assert (warning.id, warning.obj) == ("next.W129", str(page_path))
        assert "'first_open', 'second_open'" in warning.msg

    def test_a_meta_guard_leaves_the_other_action(self, root) -> None:
        write_page(root, "a", self._actions(guard=", login_required=True"))

        with routed(root):
            [warning] = check_public_actions()

        assert "'second_open'" not in warning.msg

    @pytest.mark.parametrize(
        ("guard", "declared"),
        [
            ("page.login_required(inherit=True)\n", ""),
            ("", "page.login_required()\n"),
            ("", "page.public()\n"),
            ("raise RuntimeError('boom')\n", ""),
        ],
        ids=["inherited", "page", "public", "broken"],
    )
    def test_a_rule_over_the_file_is_silent(self, root, guard, declared) -> None:
        write_guard(root, guard)
        write_page(root, "a", self._actions(declared))

        with routed(root):
            assert check_public_actions() == []

    def test_a_declaration_without_inherit_above_still_warns(self, root) -> None:
        write_guard(root, "page.login_required()\n")
        write_page(root, "a", self._actions())

        with routed(root):
            [warning] = check_public_actions()

        assert "no declaration of its page or of a page.py above it" in warning.msg

    def test_the_setting_is_silent(self, root) -> None:
        write_page(root, "a", self._actions())

        with routed(root, LOGIN_REQUIRED=True):
            assert check_public_actions() == []

    @pytest.mark.parametrize(
        ("declared", "warned"), [("page.login_required()\n", False), ("", True)]
    )
    def test_a_file_beside_a_page_answers_its_rule(
        self, root, declared: str, warned
    ) -> None:
        write_page(root, "a", page_source(declared))
        forms = root / "a" / "forms.py"
        forms.write_text(self._actions())
        load_python_module_memo(forms)

        with routed(root):
            warnings = check_public_actions()

        assert [warning.obj for warning in warnings] == ([str(forms)] if warned else [])

    def test_the_middleware_is_silent(self, root) -> None:
        write_page(root, "a", self._actions())

        with routed(root), login_required_middleware():
            assert check_public_actions() == []


@pytest.mark.usefixtures("form_registries")
class TestPublicActionsBeforeAccess:
    """next.W129 waits for the project to declare access somewhere."""

    def test_open_actions_are_silent(self, root) -> None:
        write_page(root, "a", _open_actions())
        with routed(root):
            assert check_public_actions() == []

    def test_a_component_declaration_alone_turns_it_on(self, root) -> None:
        card = root / "_components" / "card"
        card.mkdir(parents=True)
        (card / "component.py").write_text(_CARD)
        (card / "component.djx").write_text("card")
        write_page(root, "a", _open_actions())
        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            [warning] = check_public_actions()
        assert warning.id == "next.W129"


class TestGuardImports:
    """next.E017 reports a guarding `page.py` that raises while importing."""

    def test_a_broken_guard_reports_e017(self, root) -> None:
        declaring = write_guard(root / "a", "raise RuntimeError('boom')\n")
        write_page(root, "a/b", page_source())

        with routed(root):
            messages = check_page_module_imports(None)

        assert [(m.id, m.obj) for m in messages] == [("next.E017", str(declaring))]
        assert "RuntimeError: boom" in messages[0].msg

    def test_a_sound_guard_is_silent(self, root) -> None:
        write_guard(root, "page.login_required(inherit=True)\n")
        write_page(root, "a", page_source())

        with routed(root):
            assert check_page_module_imports(None) == []


_COMPONENT_ACTION = """
from next.components import component
from next.forms import action

{declaration}


@action("card_ping")
def card_ping():
    return None
"""


@pytest.mark.usefixtures("form_registries")
class TestComponentActions:
    """A `component.py` guards its actions, and a context it names refuses them."""

    @pytest.fixture()
    def card(self, root) -> Path:
        write_guard(root / "elsewhere", "page.login_required(inherit=True)\n")
        write_page(root, "", page_source("", '{% component "card" %}'))
        folder = root / "_components" / "card"
        folder.mkdir(parents=True)
        (folder / "component.djx").write_text("card")
        return folder / "component.py"

    def _check(self, root, card, declaration):
        card.write_text(_COMPONENT_ACTION.format(declaration=declaration))
        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            return check_public_actions(), check_component_action_objects()

    def test_an_undeclared_component_leaves_its_action_open(self, root, card) -> None:
        warnings, errors = self._check(root, card, "")

        assert [(item.id, item.obj) for item in warnings] == [("next.W129", str(card))]
        assert errors == []

    @pytest.mark.parametrize(
        "declaration",
        [
            "component.login_required()",
            'component.permission_required("auth.view_user")',
        ],
        ids=["login", "permission"],
    )
    def test_a_component_declaration_guards_its_action(
        self, root, card, declaration
    ) -> None:
        assert self._check(root, card, declaration) == ([], [])

    def test_a_context_object_refuses_the_action(self, root, card) -> None:
        declaration = 'component.permission_required("auth.view_user", obj="account")'

        warnings, [error] = self._check(root, card, declaration)

        assert warnings == []
        assert (error.id, error.obj) == ("next.E165", str(card))
        assert "'account'" in error.msg
        assert "'card_ping'" in error.msg
