from pathlib import Path

import pytest

from next.pages.checks.intercepts import (
    check_intercept_default_login,
    check_intercept_inert_declarations,
    check_intercept_layouts,
    check_intercept_render,
    check_intercept_scope,
    check_intercept_targets,
    check_intercept_url_kwargs,
    check_nested_intercepts,
)
from tests.support import routed, write_page
from tests.support.access import PAGE_HEAD
from tests.support.cases.intercepts import TARGET_HINT_CASES, TargetHintCase, zone_body
from tests.support.checks import located, run_check, write_files


_OPEN = 'template = "ok"\n'

_OBJECT = (
    "from django.contrib.auth.models import Group\n\n"
    "from next.deps import DObject\n"
    "from next.pages import page\n\n"
    'page.permission_required("auth.view_group", obj=DObject[Group])\n'
)


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


def _intercept(root: Path, trail: str, source: str = PAGE_HEAD) -> Path:
    return write_page(root, trail, source, body=zone_body("peek"))


class TestInterceptTargets:
    """next.E170 flags an intercept whose own tree routes no page at its target."""

    @pytest.mark.parametrize("case", TARGET_HINT_CASES, ids=lambda c: c.id)
    def test_an_unrouted_target_is_flagged_with_its_hint(
        self, root: Path, case: TargetHintCase
    ) -> None:
        write_files(root, dict(case.files))

        [message] = run_check(check_intercept_targets, root)

        assert message.id == "next.E170"
        assert case.hint in message.hint

    def test_the_message_names_the_trail_and_the_tree(self, root: Path) -> None:
        _intercept(root, "feed/(.)photo")

        [message] = run_check(check_intercept_targets, root)

        assert "targets the trail 'feed/photo'" in message.msg
        assert str(root) in message.msg
        assert Path(message.obj) == root.resolve() / "feed/(.)photo/page.py"

    def test_a_page_of_another_tree_is_named_in_the_hint(self, tmp_path: Path) -> None:
        first, second = tmp_path / "first", tmp_path / "second"
        write_page(second, "feed/photo")
        _intercept(first, "feed/(.)photo")

        with routed(first, second):
            [message] = check_intercept_targets(None)

        assert "an intercept serves only over a page of its own tree" in message.hint
        assert str(second) in message.hint

    @pytest.mark.parametrize(
        ("target", "intercept"),
        [
            ("feed/photo", "feed/(.)photo"),
            ("feed/photo/[int:id]", "feed/photo/(.)[int:id]"),
            ("photo", "feed/(..)photo"),
            ("photo", "feed/deep/(..)(..)photo"),
            ("photo", "feed/deep/(...)photo"),
            ("feed/(g)/photo", "feed/(.)photo"),
        ],
    )
    def test_a_routed_target_is_silent(
        self, root: Path, target: str, intercept: str
    ) -> None:
        write_page(root, target)
        _intercept(root, intercept)

        assert run_check(check_intercept_targets, root) == []

    def test_a_body_less_page_above_the_target_is_silent(self, root: Path) -> None:
        write_page(root, "feed/photo/[int:id]")
        write_page(
            root,
            "feed/(.)photo",
            "from next.pages import page\n\npage.public(inherit=True)\n",
        )
        _intercept(root, "feed/(.)photo/[int:id]", _OPEN)

        assert run_check(check_intercept_targets, root) == []

    def test_a_virtual_intercept_page_is_checked_too(self, root: Path) -> None:
        write_page(root, "feed")
        directory = root / "feed" / "(.)photo"
        directory.mkdir(parents=True)
        (directory / "template.djx").write_text(zone_body("peek"))

        [message] = run_check(check_intercept_targets, root)

        assert Path(message.obj) == root.resolve() / "feed/(.)photo/page.py"


class TestInterceptScope:
    """next.E171 flags a (..) intercept that climbs above its tree root."""

    @pytest.mark.parametrize(
        "trail", ["(..)x", "(g)/(..)x", "feed/(..)(..)x", "(g)/feed/(..)(..)x"]
    )
    def test_a_climb_past_the_root_is_flagged(self, root: Path, trail: str) -> None:
        write_page(root, "x")
        _intercept(root, trail)

        messages = run_check(check_intercept_scope, root)

        assert located(messages, root) == [("next.E171", trail)]

    @pytest.mark.parametrize(
        "trail", ["feed/(..)x", "(.)x", "(...)x", "feed/deep/(..)(..)x", "(g)/(.)x"]
    )
    def test_a_climb_within_the_tree_is_silent(self, root: Path, trail: str) -> None:
        write_page(root, "x")
        _intercept(root, trail)

        assert run_check(check_intercept_scope, root) == []

    def test_a_malformed_name_is_left_to_the_name_check(self, root: Path) -> None:
        write_page(root, "(..)")

        assert run_check(check_intercept_scope, root) == []

    def test_the_message_counts_the_levels(self, root: Path) -> None:
        _intercept(root, "feed/(..)(..)x")

        [message] = run_check(check_intercept_scope, root)

        assert "URL trail 'feed' of its parent" in message.msg


class TestNestedIntercepts:
    """next.E172 flags an intercept directory inside another intercept."""

    def test_an_inner_intercept_is_flagged(self, root: Path) -> None:
        write_page(root, "feed/photo")
        _intercept(root, "feed/(.)photo")
        _intercept(root, "feed/(.)photo/(.)deep")

        messages = run_check(check_nested_intercepts, root)

        assert located(messages, root) == [("next.E172", "feed/(.)photo/(.)deep")]
        assert "feed/(.)photo" in messages[0].msg

    def test_an_inner_intercept_below_a_plain_directory_is_flagged(
        self, root: Path
    ) -> None:
        write_page(root, "feed/photo")
        _intercept(root, "feed/(.)photo/sub/(..)deep")

        messages = run_check(check_nested_intercepts, root)

        assert located(messages, root) == [("next.E172", "feed/(.)photo/sub/(..)deep")]

    def test_sibling_intercepts_are_silent(self, root: Path) -> None:
        write_page(root, "feed/photo")
        _intercept(root, "feed/(.)photo")
        _intercept(root, "feed/(..)photo")

        assert run_check(check_nested_intercepts, root) == []

    def test_a_malformed_inner_name_is_left_to_the_name_check(self, root: Path) -> None:
        write_page(root, "feed/photo")
        _intercept(root, "feed/(.)photo")
        write_page(root, "feed/(.)photo/(.)")

        assert run_check(check_nested_intercepts, root) == []


class TestInterceptRender:
    """next.E175 flags an intercept page that defines render()."""

    def test_a_render_function_is_flagged(self, root: Path) -> None:
        write_page(root, "feed/photo")
        _intercept(
            root, "feed/(.)photo", f"{PAGE_HEAD}def render(request):\n    return None\n"
        )

        messages = run_check(check_intercept_render, root)

        assert located(messages, root) == [("next.E175", "feed/(.)photo/page.py")]

    def test_an_intercept_without_render_is_silent(self, root: Path) -> None:
        write_page(root, "feed/photo", f"{PAGE_HEAD}def render(request):\n    pass\n")
        _intercept(root, "feed/(.)photo")

        assert run_check(check_intercept_render, root) == []

    def test_an_unrouted_target_is_left_to_the_target_check(self, root: Path) -> None:
        _intercept(
            root, "feed/(.)photo", f"{PAGE_HEAD}def render(request):\n    pass\n"
        )

        assert run_check(check_intercept_render, root) == []

    def test_a_broken_intercept_module_is_left_to_the_import_check(
        self, root: Path
    ) -> None:
        write_page(root, "feed/photo")
        _intercept(root, "feed/(.)photo", "raise RuntimeError('boom')\n")

        assert run_check(check_intercept_render, root) == []


class TestInterceptUrlKwargs:
    """next.E176 flags a URL kwarg an intercept reads and its target lacks."""

    def test_a_bracket_directory_the_target_lacks_is_flagged(self, root: Path) -> None:
        write_page(root, "feed/photo")
        _intercept(root, "feed/[int:id]/(..)photo")

        messages = run_check(check_intercept_url_kwargs, root)

        assert located(messages, root) == [
            ("next.E176", "feed/[int:id]/(..)photo/page.py")
        ]
        assert "bracket directories 'id'" in messages[0].msg
        assert "'feed/photo' does not capture" in messages[0].msg

    def test_every_missing_kwarg_is_named_once_in_name_order(self, root: Path) -> None:
        write_page(root, "photo")
        _intercept(root, "[slug]/[int:id]/(..)(..)photo")

        [message] = run_check(check_intercept_url_kwargs, root)

        assert "bracket directories 'id', 'slug'" in message.msg

    def test_an_object_permission_the_target_lacks_is_flagged(self, root: Path) -> None:
        write_page(root, "photo")
        _intercept(root, "(.)photo", _OBJECT)

        messages = run_check(check_intercept_url_kwargs, root)

        assert located(messages, root) == [("next.E176", "(.)photo/page.py")]
        assert "DObject[Group]" in messages[0].msg
        assert "'group'" in messages[0].msg
        assert "[int:group]" in messages[0].hint

    def test_both_findings_report_separately(self, root: Path) -> None:
        write_page(root, "photo")
        _intercept(root, "[int:id]/(..)photo", _OBJECT)

        messages = run_check(check_intercept_url_kwargs, root)

        assert [message.id for message in messages] == ["next.E176", "next.E176"]

    @pytest.mark.parametrize(
        ("target", "intercept", "source"),
        [
            ("photo/[int:group]", "photo/(.)[int:group]", _OBJECT),
            ("photo/[int:group]", "photo/(.)[int:group]", PAGE_HEAD),
            ("[int:id]/photo", "[int:id]/(.)photo", PAGE_HEAD),
            ("[int:id]/photo", "[id]/(...)[int:id]/photo", PAGE_HEAD),
            ("photo", "(.)photo", PAGE_HEAD),
            ("[int:group]/photo", "[int:group]/(.)photo", _OBJECT),
        ],
    )
    def test_a_target_capturing_the_kwargs_is_silent(
        self, root: Path, target: str, intercept: str, source: str
    ) -> None:
        write_page(root, target)
        _intercept(root, intercept, source)

        assert run_check(check_intercept_url_kwargs, root) == []

    @pytest.mark.parametrize("obj", ['"auth.Group"', '"group"', "Group"])
    def test_an_obj_other_than_a_dobject_is_left_to_the_import_check(
        self, root: Path, obj: str
    ) -> None:
        # page.permission_required refuses it on import, so no rule ever reads it.
        write_page(root, "photo")
        _intercept(
            root,
            "(.)photo",
            "from django.contrib.auth.models import Group\n\n"
            f'{PAGE_HEAD}page.permission_required("auth.view_group", obj={obj})\n',
        )

        assert run_check(check_intercept_url_kwargs, root) == []

    def test_a_permission_without_an_object_is_silent(self, root: Path) -> None:
        write_page(root, "photo")
        _intercept(
            root,
            "(.)photo",
            f'{PAGE_HEAD}page.permission_required("auth.view_group")\n',
        )

        assert run_check(check_intercept_url_kwargs, root) == []


class TestInterceptInertDeclarations:
    """next.W138 warns about what an intercept declares and its zone response drops."""

    @pytest.mark.parametrize(
        ("source", "named"),
        [
            ('metadata = {"breadcrumb": "Photo"}\n', "a breadcrumb"),
            ('headers = {"X-A": "1"}\n', "headers"),
            ("cache = 60\n", "cache"),
            ('headers = {"X-A": "1"}\ncache = 60\n', "headers and cache"),
            (
                (
                    'metadata = {"breadcrumb": "Photo"}\n'
                    'headers = {"X-A": "1"}\ncache = 60\n'
                ),
                "a breadcrumb, headers and cache",
            ),
            (
                'metadata = {"breadcrumb": "Photo"}\ncache = 60\n',
                "a breadcrumb and cache",
            ),
        ],
        ids=["breadcrumb", "headers", "cache", "two", "three", "breadcrumb_cache"],
    )
    def test_what_is_declared_is_named_in_one_message(
        self, root: Path, source: str, named: str
    ) -> None:
        write_page(root, "feed/photo")
        _intercept(root, "feed/(.)photo", f"{PAGE_HEAD}{source}")

        messages = run_check(check_intercept_inert_declarations, root)

        assert located(messages, root) == [("next.W138", "feed/(.)photo/page.py")]
        assert f"declares {named}, which never take effect" in messages[0].msg

    @pytest.mark.parametrize(
        "source",
        [
            "",
            'metadata = {"title": "Photo"}\n',
            'metadata = {"title": "Photo", "breadcrumb": False}\n',
            'metadata = {"title": "Photo", "description": "d"}\n',
        ],
        ids=["nothing", "title", "no_breadcrumb", "metadata_only"],
    )
    def test_a_title_or_metadata_only_intercept_is_silent(
        self, root: Path, source: str
    ) -> None:
        write_page(root, "feed/photo")
        _intercept(root, "feed/(.)photo", f"{PAGE_HEAD}{source}")

        assert run_check(check_intercept_inert_declarations, root) == []

    def test_the_declarations_of_the_target_are_silent(self, root: Path) -> None:
        write_page(
            root,
            "feed/photo",
            f'{PAGE_HEAD}cache = 60\nheaders = {{"X-A": "1"}}\n'
            'metadata = {"breadcrumb": "Photo"}\ntemplate = "ok"\n',
        )
        _intercept(root, "feed/(.)photo")

        assert run_check(check_intercept_inert_declarations, root) == []

    def test_a_malformed_metadata_shape_is_left_to_the_metadata_checks(
        self, root: Path
    ) -> None:
        write_page(root, "feed/photo")
        _intercept(
            root, "feed/(.)photo", f'{PAGE_HEAD}metadata = {{"breadcrumb": 1}}\n'
        )

        assert run_check(check_intercept_inert_declarations, root) == []


class TestInterceptLayouts:
    """next.W139 warns about a layout.djx inside an intercept directory."""

    @pytest.mark.parametrize(
        "layout", ["feed/(.)photo/layout.djx", "feed/(.)photo/sub/layout.djx"]
    )
    def test_a_layout_in_or_below_the_intercept_is_flagged(
        self, root: Path, layout: str
    ) -> None:
        write_page(root, "feed/photo")
        _intercept(root, "feed/(.)photo")
        write_files(root, {layout: "<div>{% template %}</div>"})

        messages = run_check(check_intercept_layouts, root)

        assert located(messages, root) == [("next.W139", layout)]
        assert "feed/(.)photo" in messages[0].msg

    def test_a_layout_beside_and_above_the_intercept_is_silent(
        self, root: Path
    ) -> None:
        write_page(root, "feed/photo")
        _intercept(root, "feed/(.)photo")
        write_files(
            root,
            {
                "layout.djx": "<div>{% template %}</div>",
                "feed/layout.djx": "<div>{% template %}</div>",
                "feed/photo/layout.djx": "<div>{% template %}</div>",
            },
        )

        assert run_check(check_intercept_layouts, root) == []

    def test_a_layout_in_a_malformed_intercept_is_silent(self, root: Path) -> None:
        write_files(root, {"(.)/layout.djx": "<div>{% template %}</div>"})

        assert run_check(check_intercept_layouts, root) == []


class TestInterceptDefaultLogin:
    """next.W140 warns about an intercept asking a login over a public target."""

    @staticmethod
    def _tree(root: Path, target: str, intercept: str) -> None:
        write_page(root, "photo", f"{PAGE_HEAD}{target}\ntemplate = 'ok'\n")
        _intercept(root, "(.)photo", f"{PAGE_HEAD}{intercept}\n")

    def test_no_access_over_a_public_target_is_flagged(self, root: Path) -> None:
        self._tree(root, "page.public()", "")

        messages = run_check(check_intercept_default_login, root, LOGIN_REQUIRED=True)

        assert located(messages, root) == [("next.W140", "(.)photo/page.py")]
        assert "page.public()" in messages[0].hint

    @pytest.mark.parametrize(
        ("target", "intercept", "login"),
        [
            ("page.public()", "page.public()", True),
            ("page.public()", "page.login_required()", True),
            ("page.public()", 'page.permission_required("auth.view_group")', True),
            ("", "", True),
            ("page.login_required()", "", True),
            ("page.public()", "", False),
        ],
        ids=[
            "public_intercept",
            "login_intercept",
            "permission_intercept",
            "closed_target",
            "login_target",
            "no_default_login",
        ],
    )
    def test_a_declared_or_closed_or_open_case_is_silent(
        self, root: Path, target: str, intercept: str, *, login: bool
    ) -> None:
        self._tree(root, target, intercept)

        messages = run_check(check_intercept_default_login, root, LOGIN_REQUIRED=login)

        assert messages == []
