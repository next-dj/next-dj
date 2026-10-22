from __future__ import annotations

from dataclasses import dataclass


def zone_body(*names: str) -> str:
    """Return a template body declaring each of `names` as an own standalone zone."""
    return "".join(f'{{% zone "{name}" %}}{name}{{% endzone %}}' for name in names)


def guarded_zone_body(name: str) -> str:
    """Return a body whose zone `name` sits in a condition, so no request renders it."""
    return f'{{% if flag %}}{{% zone "{name}" %}}{name}{{% endzone %}}{{% endif %}}'


@dataclass(frozen=True, slots=True)
class PickCase:
    """One target body, the bodies of the intercepts over it and the zones asked for.

    `winner` is the index of the rule that serves them, `None` when the target does.
    """

    id: str
    target: str
    rules: tuple[str, ...]
    zones: tuple[str, ...]
    winner: int | None


PICK_CASES: tuple[PickCase, ...] = (
    PickCase("a_rule_declares_the_zone", "", (zone_body("a"),), ("a",), 0),
    PickCase("no_zone_asked", "", (zone_body("a"),), (), None),
    PickCase(
        "the_target_declares_the_zone", zone_body("a"), (zone_body("a"),), ("a",), None
    ),
    PickCase(
        "the_target_declares_one_of_two",
        zone_body("a"),
        (zone_body("a", "b"),),
        ("a", "b"),
        None,
    ),
    PickCase(
        "a_guarded_target_zone_is_no_declaration",
        guarded_zone_body("a"),
        (zone_body("a"),),
        ("a",),
        0,
    ),
    PickCase("no_rule_declares_the_zone", "", (zone_body("b"),), ("a",), None),
    PickCase(
        "a_guarded_rule_zone_is_skipped", "", (guarded_zone_body("a"),), ("a",), None
    ),
    PickCase(
        "the_first_rule_declaring_all_wins",
        "",
        (zone_body("a"), zone_body("a")),
        ("a",),
        0,
    ),
    PickCase(
        "a_rule_declaring_some_is_skipped",
        "",
        (zone_body("a"), zone_body("a", "b")),
        ("a", "b"),
        1,
    ),
    PickCase(
        "no_rule_declares_all", "", (zone_body("a"), zone_body("b")), ("a", "b"), None
    ),
)


@dataclass(frozen=True, slots=True)
class OrderCase:
    """One request to a pair of target and intercept pages and the status it earns.

    `user` names who asks, `target` and `intercept` the rule each page declares.
    """

    id: str
    user: str
    status: int
    target: str = "open"
    intercept: str = "open"
    zone: str = "peek"
    version: str | None = None
    dynamic: bool = False


ORDER_CASES: tuple[OrderCase, ...] = (
    OrderCase("both_admit", "both", 200),
    OrderCase("target_denies", "adder", 403, target="view", intercept="add"),
    OrderCase("intercept_denies", "viewer", 403, target="view", intercept="add"),
    OrderCase("both_deny", "visitor", 403, target="view", intercept="add"),
    OrderCase("anonymous_on_a_closed_target", "anonymous", 302, target="view"),
    OrderCase(
        "anonymous_before_a_stale_version",
        "anonymous",
        302,
        target="view",
        version="stale",
    ),
    OrderCase(
        "anonymous_before_an_unknown_zone",
        "anonymous",
        302,
        target="view",
        zone="ghost",
    ),
    OrderCase(
        "target_denial_before_a_stale_version",
        "visitor",
        403,
        target="view",
        version="stale",
    ),
    OrderCase(
        "target_denial_before_an_unknown_zone",
        "visitor",
        403,
        target="view",
        zone="ghost",
    ),
    OrderCase(
        "intercept_denial_before_a_stale_version",
        "viewer",
        403,
        target="view",
        intercept="add",
        version="stale",
    ),
    OrderCase("anonymous_on_a_closed_intercept", "anonymous", 302, intercept="login"),
    OrderCase("a_stale_version", "both", 409, version="stale"),
    OrderCase("an_unknown_zone", "both", 400, target="view", zone="ghost"),
    OrderCase("a_dynamic_target_serves", "both", 200, dynamic=True),
    OrderCase(
        "a_dynamic_target_denies",
        "adder",
        403,
        target="view",
        intercept="add",
        dynamic=True,
    ),
    OrderCase(
        "a_dynamic_intercept_denies",
        "viewer",
        403,
        target="view",
        intercept="add",
        dynamic=True,
    ),
    OrderCase("a_dynamic_anonymous", "anonymous", 302, target="view", dynamic=True),
    OrderCase("a_dynamic_stale_version", "both", 409, dynamic=True, version="stale"),
    OrderCase("a_dynamic_unknown_zone", "both", 400, dynamic=True, zone="ghost"),
)


@dataclass(frozen=True, slots=True)
class ObjectCase:
    """One user asking for a group row through a target and an intercept.

    Each page declares an object permission or none, `None` leaves the page open.
    """

    id: str
    user: str
    status: int
    target: str | None = None
    intercept: str | None = None
    group: str = "t:ada"


OBJECT_CASES: tuple[ObjectCase, ...] = (
    ObjectCase("intercept_only_admits", "ada", 200, intercept="auth.edit_group"),
    ObjectCase("intercept_only_refuses", "bob", 403, intercept="auth.edit_group"),
    ObjectCase("target_only_admits", "ada", 200, target="auth.edit_group"),
    ObjectCase("target_only_refuses", "bob", 403, target="auth.edit_group"),
    ObjectCase(
        "both_admit", "ada", 200, target="auth.edit_group", intercept="auth.view_group"
    ),
    ObjectCase(
        "both_refused_by_the_target",
        "bob",
        403,
        target="auth.edit_group",
        intercept="auth.view_group",
        group="t:ada",
    ),
    ObjectCase(
        "both_refused_by_the_intercept",
        "eve",
        403,
        target="auth.edit_group",
        intercept="auth.view_group",
        group="t:eve",
    ),
    ObjectCase(
        "a_row_the_scope_hides", "ada", 404, intercept="auth.edit_group", group="x:ada"
    ),
)


@dataclass(frozen=True, slots=True)
class TargetHintCase:
    """One tree whose intercept targets no routed page and the hint it earns.

    Each `files` entry maps a path under the tree root to its source.
    """

    id: str
    files: tuple[tuple[str, str], ...]
    hint: str


_ROUTED = 'template = "ok"\n'
_PLAIN = "from next.pages import page\n\n"

TARGET_HINT_CASES: tuple[TargetHintCase, ...] = (
    TargetHintCase(
        "no_page_at_the_trail",
        (("feed/(.)photo/page.py", _ROUTED),),
        "Point the marker at a directory that serves a page",
    ),
    TargetHintCase(
        "a_target_without_a_body",
        (("feed/photo/page.py", _PLAIN), ("feed/(.)photo/page.py", _ROUTED)),
        "Point the marker at a directory that serves a page",
    ),
    TargetHintCase(
        "the_parameters_spelled_differently",
        (
            ("feed/photo/[int:id]/page.py", _ROUTED),
            ("feed/photo/(.)[id]/page.py", _ROUTED),
        ),
        "'feed/photo/[int:id]', which spells the same parameters differently",
    ),
    TargetHintCase(
        "a_marker_one_level_short",
        (("photo/page.py", _ROUTED), ("feed/(.)photo/page.py", _ROUTED)),
        "Point the marker at a directory that serves a page",
    ),
)


@dataclass(frozen=True, slots=True)
class SharedZoneCase:
    """One target body, the bodies of its intercepts and the intercepts flagged.

    `flagged` lists the trails of the intercepts the check errors on, in file order.
    """

    id: str
    target: str
    intercepts: tuple[tuple[str, str], ...]
    flagged: tuple[str, ...]
    layout: str | None = None


_SHELL = '{% zone "shell" %}{% template %}{% endzone %}'

SHARED_ZONE_CASES: tuple[SharedZoneCase, ...] = (
    SharedZoneCase(
        "the_target_declares_the_zone",
        zone_body("a"),
        (("(.)photo", zone_body("a")),),
        ("(.)photo",),
    ),
    SharedZoneCase(
        "the_target_declares_one_of_two",
        zone_body("a"),
        (("(.)photo", zone_body("a", "b")),),
        ("(.)photo",),
    ),
    SharedZoneCase(
        "the_target_zone_sits_in_a_condition",
        guarded_zone_body("a"),
        (("(.)photo", zone_body("a")),),
        (),
    ),
    SharedZoneCase(
        "an_intercept_zone_in_a_condition_is_no_claim",
        zone_body("a"),
        (("(.)photo", guarded_zone_body("a") + zone_body("b")),),
        (),
    ),
    SharedZoneCase(
        "two_intercepts_share_a_zone_one_holds_in_a_condition",
        "",
        (
            ("(.)photo", guarded_zone_body("a") + zone_body("b")),
            ("feed/(..)photo", zone_body("a")),
        ),
        (),
    ),
    SharedZoneCase(
        "two_intercepts_share_a_zone",
        "",
        (("(.)photo", zone_body("a")), ("feed/(..)photo", zone_body("a"))),
        ("feed/(..)photo",),
    ),
    SharedZoneCase(
        "two_intercepts_share_one_of_two",
        "",
        (("(.)photo", zone_body("a", "b")), ("feed/(..)photo", zone_body("b", "c"))),
        ("feed/(..)photo",),
    ),
    SharedZoneCase(
        "distinct_zones",
        zone_body("a"),
        (("(.)photo", zone_body("b")), ("feed/(..)photo", zone_body("c"))),
        (),
    ),
    SharedZoneCase(
        "a_layout_zone_is_not_counted",
        "",
        (("(.)photo", zone_body("peek")), ("feed/(..)photo", zone_body("other"))),
        (),
        layout=_SHELL,
    ),
    SharedZoneCase(
        "a_layout_zone_beside_a_shared_own_zone",
        "",
        (("(.)photo", zone_body("peek")), ("feed/(..)photo", zone_body("peek"))),
        ("feed/(..)photo",),
        layout=_SHELL,
    ),
)


@dataclass(frozen=True, slots=True)
class BodyCase:
    """One intercept `page.py` and body and whether `next.E174` flags it.

    `wording` is the part of the message the error is told apart by.
    """

    id: str
    source: str
    body: str | None
    flagged: bool
    wording: str = ""


_HEAD = "from next.pages import page\n\n"

BODY_CASES: tuple[BodyCase, ...] = (
    BodyCase("no_body", _HEAD, None, True, "neither a template.djx nor a template"),
    BodyCase("no_zone", _HEAD, "<p>x</p>", True, "declares no zone it can render"),
    BodyCase(
        "a_guarded_zone", _HEAD, guarded_zone_body("peek"), True, "declares no zone"
    ),
    BodyCase("a_standalone_zone", _HEAD, zone_body("peek"), False),
    BodyCase(
        "a_template_string", f"{_HEAD}template = '{zone_body('peek')}'\n", None, False
    ),
    BodyCase(
        "a_guarded_beside_a_standalone_zone",
        _HEAD,
        guarded_zone_body("a") + zone_body("b"),
        False,
    ),
    BodyCase(
        "no_body_passing_its_access_down",
        f"{_HEAD}page.login_required(inherit=True)\n",
        None,
        False,
    ),
    BodyCase(
        "no_body_passing_its_context_down",
        (
            "from next.pages import context\n\n\n"
            '@context("pk", inherit_context=True)\n'
            "def pk() -> int:\n    return 1\n"
        ),
        None,
        False,
    ),
)
