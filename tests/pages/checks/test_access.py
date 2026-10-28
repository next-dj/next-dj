import os
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import Client

from next.forms.checks.actions import (
    check_component_action_objects,
    check_declarations_without_page,
    check_empty_branches,
    check_public_actions,
    check_render_guarded_actions,
)
from next.forms.manager import form_action_manager
from next.pages.checks.access import (
    check_declaring_modules,
    check_public_beside_requirements,
)
from next.pages.checks.modules import check_page_functions, check_page_module_imports
from next.pages.loaders import load_page_module, load_python_module_memo
from next.permissions import DeclarationOutsideModuleError
from next.permissions.access import declared_files
from tests.support import routed, write_page
from tests.support.access import (
    COMPONENTS,
    PAGE_HEAD,
    login_required_middleware,
    page_source,
    sign_in,
    write_guard,
)


_LOGIN_INHERIT = "page.login_required(inherit=True)\n"
_VIEW_INHERIT = 'page.permission_required("auth.view_group", inherit=True)\n'

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
                "page.public(inherit=True)\n" + _VIEW_INHERIT,
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
            (_LOGIN_INHERIT, "page.public()\n", []),
            (_LOGIN_INHERIT, "page.public(inherit=True)\n", []),
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

    def test_a_declaration_through_a_helper_breaks_the_page(self, root) -> None:
        page_path = write_page(
            root,
            "a",
            page_source(
                "from tests.support.access import declare_login\n\ndeclare_login()\n"
            ),
        )

        with routed(root):
            _module, error = load_page_module(page_path)
            assert check_declaring_modules() == []

        assert isinstance(error.__cause__, DeclarationOutsideModuleError)
        assert error.__cause__.source.name == "access.py"

    @pytest.mark.parametrize(
        ("guard", "page"),
        [(_LOGIN_INHERIT, "page.public()\n"), ("", "page.login_required()\n")],
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


_INHERIT_CONTEXT = (
    "from next.pages import context\n\n"
    '@context("sidebar", inherit_context=True)\n'
    "def sidebar():\n"
    '    return "menu"\n'
)

_ACTION = (
    'from next.forms import action\n\n@action("ping")\ndef ping():\n    return None\n'
)


def _branches(
    root: Path, *dirs: Path, **framework: object
) -> list[tuple[str, str, str]]:
    with routed(root, *dirs, **framework):
        return [
            (message.id, str(Path(message.obj).relative_to(root.parent)), message.msg)
            for message in check_empty_branches()
        ]


def _branch_dirs(root: Path, *dirs: Path, **framework: object) -> list[str]:
    return [directory for _id, directory, _msg in _branches(root, *dirs, **framework)]


@pytest.mark.usefixtures("form_registries")
class TestEmptyBranches:
    """next.W128 names the outermost directory that holds no page and no action."""

    def test_a_guard_over_a_page_is_silent(self, root) -> None:
        write_guard(root / "a", _LOGIN_INHERIT)
        write_page(root, "a/b", page_source())

        assert _branches(root) == []

    @pytest.mark.parametrize("trail", ["a", "empty/_drafts"], ids=["beside", "private"])
    def test_a_guard_over_no_page_warns(self, root, trail) -> None:
        guard = write_guard(root / "empty", _LOGIN_INHERIT)
        write_page(root, "c", page_source())
        write_page(root, trail, page_source())

        [(check_id, directory, message)] = _branches(root)

        assert (check_id, directory) == ("next.W128", "pages/empty")
        assert f"the inherit=True access of {guard} guards no page" in message

    @pytest.mark.parametrize(
        ("source", "reach"),
        [
            (_LOGIN_INHERIT, "guards"),
            (_VIEW_INHERIT, "guards"),
            ("page.public(inherit=True)\n", "opens"),
        ],
        ids=["login", "permission", "public"],
    )
    def test_the_text_names_what_the_access_does(self, root, source, reach) -> None:
        write_page(root, "", page_source())
        guard = write_guard(root / "a", source)

        [(_id, _directory, message)] = _branches(root)

        assert f"the inherit=True access of {guard} {reach} no page." in message

    def test_a_guard_over_another_guard_alone_warns_once(self, root) -> None:
        write_guard(root / "a", _LOGIN_INHERIT)
        write_guard(root / "a" / "b", "page.public(inherit=True)\n")
        write_page(root, "c", page_source())

        assert _branch_dirs(root) == ["pages/a"]

    def test_a_page_with_a_body_is_no_branch(self, root) -> None:
        write_page(root, "a", page_source(_LOGIN_INHERIT))

        assert _branches(root) == []

    def test_a_deep_empty_branch_warns_at_its_top(self, root) -> None:
        write_page(root, "", page_source())
        (root / "a" / "b" / "c" / "d" / "e" / "g" / "h").mkdir(parents=True)

        [(_id, directory, message)] = _branches(root)

        assert directory == "pages/a"
        assert message.endswith("holds no page and no action, so it routes nothing.")

    def test_a_layout_without_a_page_wraps_nothing(self, root) -> None:
        write_page(root, "", page_source())
        (root / "a" / "b").mkdir(parents=True)
        layout = root / "a" / "b" / "layout.djx"
        layout.write_text("<main>{% template %}</main>")

        [(_id, directory, message)] = _branches(root)

        assert directory == "pages/a"
        assert f"so {layout} wraps no page" in message

    @pytest.mark.parametrize(
        ("source", "reach"),
        [
            (
                _INHERIT_CONTEXT,
                "the inherit_context=True context of {} reaches no page",
            ),
            (
                f"{_INHERIT_CONTEXT}page.login_required(inherit=True)\n",
                (
                    "the inherit=True access and the inherit_context=True context "
                    "of {} reach no page"
                ),
            ),
        ],
        ids=["context", "both"],
    )
    def test_a_passed_context_reaches_no_page(self, root, source, reach) -> None:
        write_page(root, "", page_source())
        guard = write_guard(root / "a", source)

        [(_id, directory, message)] = _branches(root)

        assert directory == "pages/a"
        assert reach.format(guard) in message

    def test_a_passing_page_py_is_named_before_a_layout(self, root) -> None:
        write_page(root, "", page_source())
        (root / "a").mkdir()
        (root / "a" / "layout.djx").write_text("<main>{% template %}</main>")
        guard = write_guard(root / "a" / "b", _LOGIN_INHERIT)

        [(_id, _directory, message)] = _branches(root)

        assert str(guard) in message
        assert "wraps" not in message

    @pytest.mark.parametrize("trail", ["a", "a/b"], ids=["beside", "below"])
    def test_an_action_fills_its_branch(self, root, trail) -> None:
        write_page(root, "", page_source())
        write_guard(root / "a", _LOGIN_INHERIT)
        write_guard(root / trail, f"page.login_required(inherit=True)\n{_ACTION}")

        assert _branches(root) == []

    def test_private_and_component_folders_are_no_content(self, root) -> None:
        write_page(root, "", page_source())
        write_page(root, "a/_drafts", page_source())
        card = root / "a" / "_components" / "card"
        card.mkdir(parents=True)
        (card / "component.djx").write_text("card")

        assert _branch_dirs(root, COMPONENT_BACKENDS=COMPONENTS) == ["pages/a"]

    @pytest.mark.parametrize(
        "make",
        [
            lambda root: write_guard(root / "a" / "b"),
            lambda root: write_guard(root / "a" / "b", "page.login_required()\n"),
            lambda root: (root / "a" / "[slug]").mkdir(parents=True),
        ],
        ids=["no-body", "own-declaration", "parameter"],
    )
    def test_a_branch_an_error_names_is_left_to_it(self, root, make) -> None:
        write_page(root, "", page_source())
        write_guard(root / "a", _LOGIN_INHERIT)
        make(root)

        assert _branches(root) == []

    @pytest.mark.parametrize(
        "source", [_LOGIN_INHERIT, _INHERIT_CONTEXT], ids=["access", "context"]
    )
    @pytest.mark.parametrize("trail", ["a", "a/b"], ids=["top", "below"])
    def test_a_render_that_is_not_callable_is_left_to_e013(
        self, root, source, trail
    ) -> None:
        write_page(root, "", page_source())
        write_guard(root / "a", _LOGIN_INHERIT)
        write_guard(root / trail, f"{source}render = 5\n")

        with routed(root):
            found = [
                message.id
                for check in (check_page_functions, check_empty_branches)
                for message in check()
            ]

        assert found == ["next.E013"]

    def test_an_unreadable_directory_holds_nothing(self, root, monkeypatch) -> None:
        write_page(root, "", page_source())
        locked = root / "a"
        write_page(root, "a/b", page_source())
        listing = os.scandir

        def refuse(path: Path) -> object:
            if Path(path) == locked:
                raise PermissionError(path)
            return listing(path)

        monkeypatch.setattr("next.utils.os.scandir", refuse)

        assert _branch_dirs(root) == ["pages/a"]

    def test_each_empty_sibling_warns(self, root) -> None:
        write_page(root, "", page_source())
        (root / "a").mkdir()
        (root / "b").mkdir()
        write_page(root, "c", page_source())

        assert _branch_dirs(root) == ["pages/a", "pages/b"]

    def test_a_tree_without_a_page_warns_at_its_root(self, root) -> None:
        (root / "a" / "b").mkdir(parents=True)
        guard = write_guard(root, _LOGIN_INHERIT)

        [(_id, directory, message)] = _branches(root)

        assert directory == "pages"
        assert str(guard) in message

    @pytest.mark.parametrize("trail", ["", "a"], ids=["root", "nested"])
    def test_a_branch_beside_a_reported_one_still_warns(self, root, trail) -> None:
        write_page(root, "c", page_source())
        (root / trail / "[slug]").mkdir(parents=True)
        write_guard(root / trail / "admin", _LOGIN_INHERIT)

        assert _branch_dirs(root) == [str(Path("pages", trail, "admin"))]

    def test_a_pageless_param_takes_its_empty_subtree(self, root) -> None:
        write_page(root, "c", page_source())
        (root / "a" / "[slug]" / "edit").mkdir(parents=True)

        assert _branch_dirs(root) == []

    def test_a_faulty_guard_takes_its_empty_subtree(self, root) -> None:
        write_page(root, "c", page_source())
        write_guard(root / "a")
        (root / "a" / "sub").mkdir()

        assert _branch_dirs(root) == []

    def test_a_tree_root_an_error_names_is_left_to_it(self, root) -> None:
        write_guard(root)

        assert _branches(root) == []

    def test_a_shared_root_layout_keeps_its_tree(self, root) -> None:
        write_page(root, "a", page_source())
        shell = root.parent / "shell"
        (shell / "empty").mkdir(parents=True)
        (shell / "layout.djx").write_text("<main>{% template %}</main>")

        assert _branch_dirs(root, shell) == ["shell/empty"]

    def test_a_guard_beside_a_shared_root_layout_warns(self, root) -> None:
        write_page(root, "a", page_source())
        shell = root.parent / "shell"
        shell.mkdir()
        (shell / "layout.djx").write_text("<main>{% template %}</main>")
        guard = write_guard(shell, _LOGIN_INHERIT)

        [(_id, directory, message)] = _branches(root, shell)

        assert directory == "shell"
        assert str(guard) in message

    def test_a_tree_reached_twice_warns_once(self, root) -> None:
        write_page(root, "", page_source())
        (root / "a").mkdir()

        assert _branch_dirs(root, root) == ["pages/a"]

    def test_a_missing_root_is_skipped(self, root) -> None:
        write_page(root, "", page_source())

        assert _branches(root, root.parent / "missing") == []


_DECLARATIONS = {
    "none": "",
    "own": "page.login_required()\n",
    "own-public": "page.public()\n",
    "inherit": _LOGIN_INHERIT,
    "mixed": (_LOGIN_INHERIT + 'page.permission_required("auth.view_group")\n'),
}

_BODYLESS_CHECKS = {
    "none": ["next.E012"],
    "own": ["next.E166"],
    "own-public": ["next.E166"],
    "inherit": [],
    "mixed": ["next.E166"],
}

_SERVED_STATUS = {
    "none": 200,
    "own": 302,
    "own-public": 200,
    "inherit": 302,
    "mixed": 302,
}

_BODY_CHECKS = (
    check_page_functions,
    check_declarations_without_page,
    check_empty_branches,
)


class TestBodylessDeclarations:
    """A `page.py` routes by its own body alone, and a `layout.djx` is none."""

    @pytest.mark.parametrize("layout", ["above", "beside", "dirs-root", "absent"])
    @pytest.mark.parametrize("declared", list(_DECLARATIONS))
    @pytest.mark.parametrize("body", [True, False], ids=["body", "bodyless"])
    def test_routing_and_checks(
        self, root, *, body: bool, declared: str, layout: str
    ) -> None:
        source = _DECLARATIONS[declared]
        write_page(root, "a", page_source(source) if body else f"{PAGE_HEAD}{source}")
        write_page(root, "a/b", page_source("page.public()\n"))
        shell = root.parent / "shell"
        shell.mkdir()
        targets = {"above": root, "beside": root / "a", "dirs-root": shell}
        if layout in targets:
            (targets[layout] / "layout.djx").write_text("<main>{% template %}</main>")
        dirs = [shell] if layout == "dirs-root" else []

        with routed(root, *dirs):
            status = Client().get("/a/").status_code
            found = [
                message.id
                for check in _BODY_CHECKS
                for message in check()
                if message.id in {"next.E012", "next.E166", "next.W128"}
            ]

        assert status == (_SERVED_STATUS[declared] if body else 404)
        assert found == ([] if body else _BODYLESS_CHECKS[declared])

    def test_the_error_names_both_fixes(self, root) -> None:
        page_path = write_guard(root / "a", "page.login_required()\n")

        with routed(root):
            [error] = check_declarations_without_page()

        assert (error.id, error.obj) == ("next.E166", str(page_path))
        assert "protects nothing" in error.msg
        assert "inherit=True" in error.hint
        assert "template" in error.hint

    def test_public_beside_a_requirement_is_reported_once(self, root) -> None:
        write_page(root, "", page_source())
        page_path = write_guard(root / "a", "page.public()\npage.login_required()\n")

        with routed(root):
            found = [
                *check_public_beside_requirements(),
                *check_declarations_without_page(),
            ]

        assert [(message.id, message.obj) for message in found] == [
            ("next.E163", str(page_path))
        ]

    @pytest.mark.usefixtures("form_registries")
    def test_a_bodyless_page_below_a_guard_is_no_page(self, root) -> None:
        write_guard(root / "a", _LOGIN_INHERIT)
        lone = write_guard(root / "a" / "b", "page.login_required()\n")

        with routed(root):
            found = [*check_declarations_without_page(), *check_empty_branches()]

        assert [(message.id, message.obj) for message in found] == [
            ("next.E166", str(lone))
        ]

    @pytest.mark.usefixtures("form_registries")
    @pytest.mark.parametrize("declared", ["own", "own-public", "mixed"])
    def test_an_action_of_the_directory_is_what_it_protects(
        self, root, declared: str
    ) -> None:
        source = _DECLARATIONS[declared]
        write_guard(
            root / "a",
            "from next.forms import action\n\n"
            f'{source}\n@action("ping")\ndef ping():\n    pass\n',
        )
        lone = write_guard(root / "b", source)

        with routed(root):
            [error] = check_declarations_without_page()

        assert (error.id, error.obj) == ("next.E166", str(lone))

    def test_a_public_inherit_beside_a_requirement_is_left_to_e163(self, root) -> None:
        write_page(root, "a/b", page_source())
        write_guard(
            root / "a", "page.public(inherit=True)\npage.login_required(inherit=True)\n"
        )

        with routed(root):
            found = [
                *check_declarations_without_page(),
                *check_public_beside_requirements(),
            ]

        assert [message.id for message in found] == ["next.E163"]

    @pytest.mark.parametrize(
        "source", ["", "raise RuntimeError('boom')\n"], ids=["virtual", "broken"]
    )
    def test_a_virtual_or_broken_page_is_not_judged(self, root, source) -> None:
        directory = root / "a"
        directory.mkdir()
        (directory / "template.djx").write_text("ok")
        if source:
            (directory / "page.py").write_text(source)

        with routed(root):
            assert check_declarations_without_page() == []


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
        write_guard(root / "elsewhere", _LOGIN_INHERIT)

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
            (_LOGIN_INHERIT, ""),
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


_RENDER = "\ndef render(request):\n    return 'ok'\n"


@pytest.mark.usefixtures("form_registries")
class TestRenderGuardedActions:
    """next.W150 warns about actions whose page guards them only in `render()`."""

    def test_actions_beside_a_render_warn_once_per_file(self, root) -> None:
        page_path = write_page(root, "a", _open_actions() + _RENDER)

        with routed(root):
            [warning] = check_render_guarded_actions()

        assert (warning.id, warning.obj) == ("next.W150", str(page_path))
        assert "'first_open', 'second_open'" in warning.msg

    def test_a_meta_guard_leaves_the_other_action(self, root) -> None:
        write_page(root, "a", _open_actions(guard=", login_required=True") + _RENDER)

        with routed(root):
            [warning] = check_render_guarded_actions()

        assert "'second_open'" not in warning.msg

    @pytest.mark.parametrize(
        ("declared", "render"),
        [("page.login_required()\n", _RENDER), ("page.public()\n", _RENDER), ("", "")],
        ids=["declared", "public", "no-render"],
    )
    def test_a_declaration_or_no_render_is_silent(
        self, root, declared: str, render: str
    ) -> None:
        write_page(root, "a", _open_actions(declared) + render)

        with routed(root):
            assert check_render_guarded_actions() == []

    def test_the_setting_is_silent(self, root) -> None:
        write_page(root, "a", _open_actions() + _RENDER)

        with routed(root, LOGIN_REQUIRED=True):
            assert check_render_guarded_actions() == []


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
        write_guard(root, _LOGIN_INHERIT)
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
        write_guard(root / "elsewhere", _LOGIN_INHERIT)
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


_STAFF_PERMISSION = "auth.is_staff"


def _admin_tree(root: Path, source: str) -> None:
    write_guard(root / "admin", source)
    accounts = root / "admin" / "accounts"
    accounts.mkdir()
    (accounts / "template.djx").write_text("<p>{{ sidebar }}</p>")


def _branch_messages() -> list[str]:
    return [
        message.id
        for check in _BODY_CHECKS
        for message in check()
        if message.id in {"next.E012", "next.E166", "next.W128"}
    ]


def _serious() -> list[str]:
    return [
        message.id
        for check in (*_BODY_CHECKS, check_page_module_imports)
        for message in check()
        if message.is_serious()
    ]


@pytest.mark.django_db()
@pytest.mark.usefixtures("form_registries")
class TestSubtreePageModules:
    """A `page.py` with no body that passes access or context serves its subtree."""

    def test_a_guard_serves_the_pages_below(self, root) -> None:
        _admin_tree(
            root, f'page.permission_required("{_STAFF_PERMISSION}", inherit=True)\n'
        )
        Permission.objects.create(
            codename="is_staff",
            name="Is staff",
            content_type=ContentType.objects.get_for_model(get_user_model()),
        )
        staff, visitor = Client(), Client()
        sign_in(staff, "staff", "is_staff")
        sign_in(visitor, "visitor")

        with routed(root):
            serious = _serious()
            found = _branch_messages()
            statuses = [
                Client().get("/admin/").status_code,
                staff.get("/admin/accounts/").status_code,
                visitor.get("/admin/accounts/").status_code,
            ]
            anonymous = Client().get("/admin/accounts/")

        assert serious == []
        assert found == []
        assert statuses == [404, 200, 403]
        assert anonymous.status_code == 302
        assert anonymous["Location"].startswith(settings.LOGIN_URL)

    def test_a_context_reaches_the_pages_below(self, root) -> None:
        _admin_tree(root, _INHERIT_CONTEXT)

        with routed(root):
            index = Client().get("/admin/")
            accounts = Client().get("/admin/accounts/")
            serious = _serious()
            found = _branch_messages()

        assert serious == []
        assert found == []
        assert index.status_code == 404
        assert accounts.status_code == 200
        assert "<p>menu</p>" in accounts.content.decode()

    def test_a_page_module_passing_nothing_is_an_error(self, root) -> None:
        _admin_tree(root, "")

        with routed(root):
            found = _branch_messages()
            serious = _serious()

        assert found == ["next.E012"]
        assert serious == ["next.E012"]


@pytest.mark.usefixtures("form_registries")
class TestActionPages:
    """A `page.py` with no body whose directory declares an action serves the action."""

    @pytest.mark.parametrize("guard", [None, _LOGIN_INHERIT], ids=["alone", "below"])
    def test_an_action_alone_is_no_error(self, root, guard) -> None:
        write_page(root, "", page_source())
        if guard is not None:
            write_guard(root / "a", guard)
        write_guard(root / "a" / "b", _ACTION)

        with routed(root):
            found = _branch_messages()
            serious = _serious()

        assert found == []
        assert serious == []

    def test_the_action_answers_a_post(self, root) -> None:
        write_page(root, "", page_source())
        lone = write_guard(root / "a", _ACTION)

        with routed(root):
            Client().get("/")
            url = form_action_manager.get_action_url("ping", page_path=str(lone))
            response = Client().post(url, {})

        assert response.status_code == 204

    def test_an_action_of_another_directory_clears_nothing(self, root) -> None:
        write_page(root, "", page_source())
        write_guard(root / "a", _ACTION)
        write_guard(root / "b")

        with routed(root):
            [error] = check_page_functions()

        assert error.id == "next.E012"
        assert error.msg.startswith("Root pages: b/page.py has no body source")
        assert "its directory declares an action" in error.hint
