from pathlib import Path

import pytest
from django.test import Client

from next.forms.checks.actions import check_public_actions
from next.pages.checks.access import (
    check_declaring_modules,
    check_guarded_components_on_public_pages,
    check_lifted_requirements,
    check_proxies_over_pages,
    check_public_beside_requirements,
)
from next.pages.checks.modules import check_page_module_imports
from next.pages.loaders import load_python_module_memo
from next.permissions.access import declared_files
from tests.support import isolated_form_registries, routed, write_page
from tests.support.access import (
    COMPONENTS,
    login_required_middleware,
    page_source,
    write_proxy,
)


pytestmark = pytest.mark.usefixtures("isolated_declarations")

_CARD = (
    "from next.components import component\n\n"
    'component.permission_required("auth.view_group")\n'
)


@pytest.fixture()
def root(tmp_path) -> Path:
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


def _ids(messages) -> list[tuple[str, str]]:
    return [(message.id, Path(message.obj).name) for message in messages]


class TestPublicBesideRequirements:
    """next.E165 flags a `public()` beside a requirement of the same file."""

    @pytest.mark.parametrize(
        ("proxy", "page", "expected"),
        [
            ("", "page.public()\n", []),
            ("", "page.public()\npage.login_required()\n", [("next.E165", "page.py")]),
            (
                'proxy.public()\nproxy.permission_required("auth.view_group")\n',
                "",
                [("next.E165", "proxy.py")],
            ),
            ("proxy.login_required()\n", "page.public()\n", []),
        ],
        ids=["public", "page", "proxy", "lift"],
    )
    def test_one_file_with_both(self, root, proxy, page, expected) -> None:
        write_proxy(root, proxy)
        write_page(root, "a", page_source(page))

        with routed(root):
            assert _ids(check_public_beside_requirements()) == expected


class TestLiftedRequirements:
    """next.W135 names each `public()` that lifts the rule of a guarded parent."""

    def test_a_lifting_proxy_and_page_each_warn(self, root) -> None:
        guard = write_proxy(root, "proxy.login_required()\n")
        lifter = write_proxy(root / "a", "proxy.public()\n")
        write_page(root, "a", page_source())
        write_page(root, "a/b", page_source())
        page_path = write_page(root, "c", page_source("page.public()\n"))

        with routed(root):
            warnings = check_lifted_requirements()

        assert [(w.id, w.obj) for w in warnings] == [
            ("next.W135", str(lifter)),
            ("next.W135", str(page_path)),
        ]
        assert f"public() in {lifter} lifts the requirements of {guard}" in (
            warnings[0].msg
        )
        assert "its directory and every page below it" in warnings[0].msg
        assert "for its page" in warnings[1].msg

    @pytest.mark.parametrize(
        ("proxy", "page"),
        [
            ("", "page.public()\n"),
            ("proxy.login_required()\n", ""),
            ("raise RuntimeError('boom')\n", "page.public()\n"),
        ],
        ids=["nothing-above", "no-public", "broken"],
    )
    def test_nothing_lifted_is_silent(self, root, proxy, page) -> None:
        write_proxy(root, proxy)
        write_page(root, "a", page_source(page))

        with routed(root):
            assert check_lifted_requirements() == []

    def test_a_guard_below_the_lift_is_silent_for_its_pages(self, root) -> None:
        write_proxy(root, "proxy.login_required()\n")
        write_proxy(root / "a", "proxy.public()\n")
        write_proxy(root / "a" / "b", "proxy.login_required()\n")
        write_page(root, "a/b", page_source())

        with routed(root):
            assert check_lifted_requirements() == []


class TestDeclaringModules:
    """next.E166 flags a declaration bound to a file no endpoint reads."""

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

        assert (error.id, Path(error.obj).name) == ("next.E166", "access.py")
        assert "helper" in error.hint

    @pytest.mark.parametrize(
        ("proxy", "page"),
        [
            ("proxy.login_required()\n", "page.public()\n"),
            ("", "page.login_required()\n"),
        ],
        ids=["proxy-and-page", "page"],
    )
    def test_segment_modules_are_silent(self, root, proxy, page) -> None:
        write_proxy(root, proxy)
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
            assert card / "component.py" in declared_files()
            assert check_declaring_modules() == []


class TestProxiesOverPages:
    """next.W132 warns about a `proxy.py` that reaches no page."""

    def test_a_proxy_over_a_page_is_silent(self, root) -> None:
        write_proxy(root / "a", "proxy.login_required()\n")
        write_page(root, "a/b", page_source())

        with routed(root):
            assert check_proxies_over_pages() == []

    def test_a_proxy_beside_no_page_warns(self, root) -> None:
        write_proxy(root / "empty", "proxy.login_required()\n")
        write_page(root, "a", page_source())

        with routed(root):
            assert _ids(check_proxies_over_pages()) == [("next.W132", "proxy.py")]

    def test_a_proxy_in_a_skipped_folder_is_walked(self, root) -> None:
        write_proxy(root / "_private", "proxy.login_required()\n")
        write_page(root, "a", page_source())

        with routed(root):
            assert _ids(check_proxies_over_pages()) == [("next.W132", "proxy.py")]

    def test_a_proxy_in_a_skipped_folder_is_checked(self, root) -> None:
        write_proxy(root / "_private", "proxy.public()\nproxy.login_required()\n")
        write_page(root, "a", page_source())

        with routed(root):
            assert _ids(check_public_beside_requirements()) == [
                ("next.E165", "proxy.py")
            ]


class TestGuardedComponentsOnPublicPages:
    """next.W133 warns about a declaring component on a page anyone reaches."""

    @pytest.fixture()
    def card(self, root) -> Path:
        folder = root / "_components" / "card"
        folder.mkdir(parents=True)
        (folder / "component.djx").write_text("card")
        plain = root / "_components" / "plain"
        plain.mkdir()
        (plain / "component.djx").write_text("plain")
        bare = root / "_components" / "bare"
        bare.mkdir()
        (bare / "component.djx").write_text("bare")
        (bare / "component.py").write_text("x = 1\n")
        module = folder / "component.py"
        module.write_text(_CARD)
        return module

    @pytest.mark.parametrize(
        ("page", "expected"),
        [
            ("page.public()\n", ["next.W133"]),
            ("", ["next.W133"]),
            ("page.login_required()\n", []),
        ],
        ids=["public", "open", "guarded"],
    )
    def test_the_page_rule_decides(self, root, card, page, expected) -> None:
        tags = '{% component "card" %}{% component "plain" %}{% component "bare" %}'
        write_page(root, "a", page_source(page, tags))

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            assert [
                w.id for w in check_guarded_components_on_public_pages()
            ] == expected

    @pytest.mark.parametrize(
        ("holder", "via"),
        [("page", "include"), ("component", "include"), ("component", "extends")],
        ids=["page-include", "component-include", "component-extends"],
    )
    def test_a_component_reached_through_a_template_warns(
        self, root, card, findable_templates, holder, via
    ) -> None:
        part = findable_templates("part.html", '{% component "card" %}')
        tag = f'{{% {via} "{part}" %}}'
        if holder == "component":
            (root / "_components" / "plain" / "component.djx").write_text(tag)
            tag = '{% component "plain" %}'
        write_page(root, "a", page_source("", tag))

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            assert [w.id for w in check_guarded_components_on_public_pages()] == [
                "next.W133"
            ]

    def test_the_middleware_closes_an_open_page(self, root, card) -> None:
        write_page(root, "a", page_source("", '{% component "card" %}'))

        with routed(root, COMPONENT_BACKENDS=COMPONENTS), login_required_middleware():
            assert check_guarded_components_on_public_pages() == []

    def test_a_broken_chain_is_left_to_its_error(self, root, card) -> None:
        write_proxy(root, "raise RuntimeError('boom')\n")
        write_page(root, "a", page_source("", '{% component "card" %}'))

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            assert check_guarded_components_on_public_pages() == []


class TestPublicActions:
    """next.W134 warns once per file about the actions nothing guards."""

    @pytest.fixture(autouse=True)
    def _form_registries(self):
        with isolated_form_registries():
            yield

    @staticmethod
    def _actions(declared: str = "", guard: str = "") -> str:
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

    def test_open_actions_warn_once_per_file(self, root) -> None:
        page_path = write_page(root, "a", self._actions())

        with routed(root):
            [warning] = check_public_actions()

        assert (warning.id, warning.obj) == ("next.W134", str(page_path))
        assert "'first_open', 'second_open'" in warning.msg

    def test_a_meta_guard_leaves_the_other_action(self, root) -> None:
        write_page(root, "a", self._actions(guard=", login_required=True"))

        with routed(root):
            [warning] = check_public_actions()

        assert "'second_open'" not in warning.msg

    @pytest.mark.parametrize(
        ("proxy", "declared"),
        [
            ("proxy.login_required()\n", ""),
            ("", "page.login_required()\n"),
            ("", "page.public()\n"),
            ("raise RuntimeError('boom')\n", ""),
        ],
        ids=["proxy", "page", "public", "broken"],
    )
    def test_a_rule_over_the_file_is_silent(self, root, proxy, declared) -> None:
        write_proxy(root, proxy)
        write_page(root, "a", self._actions(declared))

        with routed(root):
            assert check_public_actions() == []

    def test_a_proxy_without_requirements_still_warns(self, root) -> None:
        write_proxy(root, "")
        write_page(root, "a", self._actions())

        with routed(root):
            [warning] = check_public_actions()

        assert "no requirement from a proxy.py above it" in warning.msg

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


class TestProxyImports:
    """next.E017 reports a `proxy.py` that raises while importing."""

    def test_a_broken_proxy_reports_e017(self, root) -> None:
        declaring = write_proxy(root / "a", "raise RuntimeError('boom')\n")
        write_page(root, "a", page_source())

        with routed(root):
            messages = check_page_module_imports(None)

        assert [(m.id, m.obj) for m in messages] == [("next.E017", str(declaring))]
        assert "RuntimeError: boom" in messages[0].msg

    def test_a_sound_proxy_is_silent(self, root) -> None:
        write_proxy(root, "proxy.login_required()\n")
        write_page(root, "a", page_source())

        with routed(root):
            assert check_page_module_imports(None) == []

    def test_a_proxy_in_a_skipped_folder_reports_e017(self, root) -> None:
        declaring = write_proxy(root / "_drafts", "raise RuntimeError('boom')\n")
        write_page(root, "a", page_source())

        with routed(root):
            messages = check_page_module_imports(None)

        assert [(m.id, m.obj) for m in messages] == [("next.E017", str(declaring))]
