from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DirectoryNameCase:
    """One parenthesised directory name and whether `next.E169` flags it.

    `reserved` tells the intercept wording from the route group one.
    """

    name: str
    flagged: bool
    reserved: bool = False


DIRECTORY_NAME_CASES: tuple[DirectoryNameCase, ...] = (
    DirectoryNameCase("()", True),
    DirectoryNameCase("(a b)", True),
    DirectoryNameCase("((x))", True),
    DirectoryNameCase("(x", True),
    DirectoryNameCase("x)", True),
    DirectoryNameCase("([id])", True),
    DirectoryNameCase("(1x)", True),
    DirectoryNameCase("(.)", True, reserved=True),
    DirectoryNameCase("(..)", True, reserved=True),
    DirectoryNameCase("(.)(x)", True, reserved=True),
    DirectoryNameCase("(.)(..)x", True, reserved=True),
    DirectoryNameCase("(app)", False),
    DirectoryNameCase("(shop-admin)", False),
    DirectoryNameCase("(_private)", False),
    DirectoryNameCase("(.)photo", False),
    DirectoryNameCase("(..)photo", False),
    DirectoryNameCase("(...)photo", False),
)


@dataclass(frozen=True, slots=True)
class PointlessGroupCase:
    """One tree under a `(g)` group and whether `next.W137` flags the group.

    Each entry of `files` is written as an empty file, relative to the tree root.
    """

    id: str
    files: tuple[str, ...]
    flagged: bool


POINTLESS_GROUP_CASES: tuple[PointlessGroupCase, ...] = (
    PointlessGroupCase("one_routed_child", ("(g)/x/page.py",), True),
    PointlessGroupCase(
        "a_hidden_file_is_no_contribution", ("(g)/.keep", "(g)/x/page.py"), True
    ),
    PointlessGroupCase(
        "a_bytecode_cache_is_no_child", ("(g)/__pycache__/x.pyc", "(g)/x/page.py"), True
    ),
    PointlessGroupCase("a_layout", ("(g)/layout.djx", "(g)/x/page.py"), False),
    PointlessGroupCase("a_page", ("(g)/page.py", "(g)/x/page.py"), False),
    PointlessGroupCase("a_template", ("(g)/template.djx", "(g)/x/page.py"), False),
    PointlessGroupCase(
        "components", ("(g)/_components/card/component.djx", "(g)/x/page.py"), False
    ),
    PointlessGroupCase("a_second_child", ("(g)/x/page.py", "(g)/y/page.py"), False),
    PointlessGroupCase("an_intercept_child", ("x/page.py", "(g)/(.)x/page.py"), False),
    PointlessGroupCase(
        "no_routed_child", ("(g)/_components/card/component.djx",), False
    ),
    PointlessGroupCase("an_empty_group", ("(g)/.keep",), False),
)


@dataclass(frozen=True, slots=True)
class CrumbCase:
    """One page directory and metadata source and whether `next.W136` flags the page.

    `tree` is the name of the tree root directory.
    """

    id: str
    directory: str
    source: str
    flagged: bool
    tree: str = "pages"


_CALLABLE_METADATA = (
    "from next.pages import page\n\n\n"
    "@page.metadata\n"
    'def metadata() -> dict:\n    return {"title": "T", "breadcrumb": "Home"}\n'
)

CRUMB_CASES: tuple[CrumbCase, ...] = (
    CrumbCase(
        "a_label", "(g)", 'metadata = {"title": "T", "breadcrumb": "Home"}\n', True
    ),
    CrumbCase(
        "a_label_in_a_nested_group",
        "(g)/(h)",
        'metadata = {"breadcrumb": "Home"}\n',
        True,
    ),
    CrumbCase("a_title_only", "(g)", 'metadata = {"title": "T"}\n', False),
    CrumbCase("no_breadcrumb", "(g)", 'metadata = {"breadcrumb": False}\n', False),
    CrumbCase("a_callable_declaration", "(g)", _CALLABLE_METADATA, False),
    CrumbCase("a_routed_page", "about", 'metadata = {"breadcrumb": "About"}\n', False),
    CrumbCase(
        "a_group_at_the_tree_root",
        "",
        'metadata = {"breadcrumb": "Home"}\n',
        False,
        tree="(g)",
    ),
)


@dataclass(frozen=True, slots=True)
class ClashCase:
    """The page trails of each tree that serve one URL and whether the clash is hinted.

    Only a clash with a source that passes through a route group earns a hint.
    """

    id: str
    trees: tuple[tuple[str, ...], ...]
    hinted: bool


CLASH_CASES: tuple[ClashCase, ...] = (
    ClashCase("two_groups_of_one_tree", (("(a)/x", "(b)/x"),), True),
    ClashCase("a_group_page_and_its_parent_page", (("", "(g)"),), True),
    ClashCase("a_nested_group", (("(a)/(b)/x", "x"),), True),
    ClashCase("a_group_beside_a_plain_tree", (("(a)/x",), ("x",)), True),
    ClashCase("a_segment_split_across_a_group", (("shop/x", "(g)/shop/x"),), True),
    ClashCase("two_plain_trees", (("x",), ("x",)), False),
    ClashCase("two_plain_pages_of_equal_routes", (("[id]",), ("[str:id]",)), False),
)
