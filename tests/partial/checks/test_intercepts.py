from collections.abc import Callable
from pathlib import Path

import pytest
from django.core.checks import CheckMessage

from next.pages.checks.intercepts import (
    check_intercept_default_login,
    check_intercept_inert_declarations,
    check_intercept_render,
    check_intercept_url_kwargs,
)
from next.partial.checks.intercepts import (
    check_intercept_bodies,
    check_intercept_zones_unique,
    own_zones,
)
from next.ports import action_owners_slot
from tests.pages.checks.metadata.trees import component_template
from tests.support import write_page
from tests.support.access import PAGE_HEAD
from tests.support.cases.intercepts import (
    BODY_CASES,
    SHARED_ZONE_CASES,
    BodyCase,
    SharedZoneCase,
    guarded_zone_body,
    zone_body,
)
from tests.support.checks import located, run_check, write_files
from tests.support.guards import GUARDED_CHILDREN, wrap


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


class TestInterceptZonesUnique:
    """next.E173 flags an intercept zone its target or a sibling intercept declares."""

    @pytest.mark.parametrize("case", SHARED_ZONE_CASES, ids=lambda c: c.id)
    def test_the_later_declaration_is_flagged(
        self, root: Path, case: SharedZoneCase
    ) -> None:
        write_page(root, "photo", PAGE_HEAD, body=case.target)
        for trail, body in case.intercepts:
            write_page(root, trail, PAGE_HEAD, body=body)
        if case.layout is not None:
            write_files(root, {"layout.djx": case.layout})

        messages = run_check(check_intercept_zones_unique, root)

        assert located(messages, root) == [
            ("next.E173", f"{trail}/page.py") for trail in case.flagged
        ]

    def test_the_target_message_names_the_zones_and_the_target(
        self, root: Path
    ) -> None:
        write_page(root, "photo", PAGE_HEAD, body=zone_body("a", "b"))
        write_page(root, "(.)photo", PAGE_HEAD, body=zone_body("b", "a", "c"))

        [message] = run_check(check_intercept_zones_unique, root)

        assert "declares the zones 'a', 'b'" in message.msg
        assert str(root / "photo") in message.msg
        assert "Rename the zone" in message.hint

    def test_the_sibling_message_names_both_files_and_the_zones(
        self, root: Path
    ) -> None:
        write_page(root, "photo")
        write_page(root, "(.)photo", PAGE_HEAD, body=zone_body("a", "b"))
        write_page(root, "feed/(..)photo", PAGE_HEAD, body=zone_body("b", "a"))

        [message] = run_check(check_intercept_zones_unique, root)

        assert "both serve the zones 'a', 'b'" in message.msg
        assert "(.)photo/page.py and" in message.msg
        assert "Give each intercept of one target zones of its own" in message.hint

    def test_a_target_zone_inside_a_binding_component_is_no_claim(
        self, root: Path
    ) -> None:
        component_template(root, "guarded", GUARDED_CHILDREN)
        write_page(root, "photo", PAGE_HEAD, body=wrap("guarded", zone_body("modal")))
        write_page(root, "(.)photo", PAGE_HEAD, body=zone_body("modal"))

        assert run_check(check_intercept_zones_unique, root) == []

    def test_two_targets_may_reuse_a_zone_name(self, root: Path) -> None:
        write_page(root, "one")
        write_page(root, "two")
        write_page(root, "(.)one", PAGE_HEAD, body=zone_body("peek"))
        write_page(root, "(.)two", PAGE_HEAD, body=zone_body("peek"))

        assert run_check(check_intercept_zones_unique, root) == []

    def test_an_intercept_that_fails_to_compose_is_left_to_the_template_check(
        self, root: Path
    ) -> None:
        write_page(root, "photo", PAGE_HEAD, body=zone_body("a"))
        write_page(root, "(.)photo", PAGE_HEAD, body="{% if %}")

        assert run_check(check_intercept_zones_unique, root) == []

    def test_a_target_that_fails_to_compose_is_left_to_the_template_check(
        self, root: Path
    ) -> None:
        write_page(root, "photo", PAGE_HEAD, body="{% if %}")
        write_page(root, "(.)photo", PAGE_HEAD, body=zone_body("a"))

        assert run_check(check_intercept_zones_unique, root) == []

    def test_a_layout_that_fails_to_compose_is_left_to_the_template_check(
        self, root: Path
    ) -> None:
        write_files(root, {"layout.djx": "{% if %}{% template %}"})
        write_page(root, "photo", PAGE_HEAD, body=zone_body("a"))
        write_page(root, "(.)photo", PAGE_HEAD, body=zone_body("a"))

        assert run_check(check_intercept_zones_unique, root) == []


class TestInterceptBodies:
    """next.E174 flags an intercept with no template or no standalone zone."""

    @pytest.mark.parametrize("case", BODY_CASES, ids=lambda c: c.id)
    def test_a_body_is_flagged_or_silent(self, root: Path, case: BodyCase) -> None:
        write_page(root, "photo")
        write_page(root, "(.)photo", case.source, body=case.body)

        messages = run_check(check_intercept_bodies, root)

        expected = [("next.E174", "(.)photo/page.py")] if case.flagged else []
        assert located(messages, root) == expected
        assert all(case.wording in message.msg for message in messages)

    def test_a_virtual_intercept_with_a_zone_is_silent(self, root: Path) -> None:
        write_page(root, "photo")
        write_files(root, {"(.)photo/template.djx": zone_body("peek")})

        assert run_check(check_intercept_bodies, root) == []

    def test_a_template_that_fails_to_compose_is_left_to_the_template_check(
        self, root: Path
    ) -> None:
        write_page(root, "photo")
        write_page(root, "(.)photo", PAGE_HEAD, body="{% if %}")

        assert run_check(check_intercept_bodies, root) == []

    def test_a_zone_a_layout_declares_is_no_zone_of_the_intercept(
        self, root: Path
    ) -> None:
        write_files(
            root, {"layout.djx": '{% zone "shell" %}{% template %}{% endzone %}'}
        )
        write_page(root, "photo")
        write_page(root, "(.)photo", PAGE_HEAD, body="<p>x</p>")

        messages = run_check(check_intercept_bodies, root)

        assert located(messages, root) == [("next.E174", "(.)photo/page.py")]


_CARRIER = (
    "from django.contrib.auth.models import Group\n\n"
    "from next.deps import DObject\n"
    "from next.pages import context, page\n\n\n"
    'page.permission_required("auth.view_group", obj=DObject[Group], inherit=True)\n'
    'metadata = {"robots": {"index": False}}\n\n\n'
    '@context("group", inherit_context=True)\n'
    "def group(group: DObject[Group]) -> Group:\n    return group\n"
)


class _Owners:
    def __init__(self, *files: Path) -> None:
        self._files = frozenset(
            path for file in files for path in (file, file.resolve())
        )

    def page_files(self) -> frozenset[Path]:
        return self._files


class TestBodyLessCarrier:
    """A body-less page.py at a routed target trail passes declarations down."""

    @pytest.fixture()
    def carrier(self, root: Path) -> Path:
        # The carrier sits at the routed trail `request` and passes down to the peek.
        write_page(root, "request")
        write_page(root, "request/[int:group]/audit")
        page_file = write_page(root, "(.)request", _CARRIER)
        write_page(
            root, "(.)request/[int:group]/audit", PAGE_HEAD, body=zone_body("peek")
        )
        return page_file

    @pytest.mark.parametrize(
        "check",
        [
            check_intercept_bodies,
            check_intercept_zones_unique,
            check_intercept_render,
            check_intercept_url_kwargs,
            check_intercept_inert_declarations,
            check_intercept_default_login,
        ],
    )
    def test_no_intercept_check_flags_it(
        self, root: Path, carrier: Path, check: Callable[..., list[CheckMessage]]
    ) -> None:
        assert run_check(check, root, LOGIN_REQUIRED=True) == []

    def test_a_context_carrier_over_a_public_target_asks_no_login(
        self, root: Path
    ) -> None:
        write_page(root, "request", f"{PAGE_HEAD}page.public()\ntemplate = 'ok'\n")
        write_page(
            root,
            "(.)request",
            "from next.pages import context\n\n\n"
            '@context("pk", inherit_context=True)\n'
            "def pk() -> int:\n    return 1\n",
        )

        assert run_check(check_intercept_default_login, root, LOGIN_REQUIRED=True) == []

    def test_a_body_less_page_passing_nothing_is_flagged(self, root: Path) -> None:
        write_page(root, "request")
        write_page(root, "(.)request", PAGE_HEAD)

        [message] = run_check(check_intercept_bodies, root)

        assert "passes nothing to the pages below" in message.msg
        assert "inherit=True" in message.hint

    def test_a_body_less_page_owning_an_action_is_silent(
        self, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_page(root, "request")
        page_file = write_page(root, "(.)request", PAGE_HEAD)
        monkeypatch.setattr(action_owners_slot, "_impl", _Owners(page_file))

        assert run_check(check_intercept_bodies, root) == []


class TestOwnZones:
    """own_zones reads the zones of a body and leaves out the ones its layouts hold."""

    def test_the_layout_zone_is_left_out(self, root: Path) -> None:
        write_files(
            root, {"layout.djx": '{% zone "shell" %}{% template %}{% endzone %}'}
        )
        page_file = write_page(root, "photo", PAGE_HEAD, body=zone_body("peek"))

        zones = own_zones(page_file)

        assert zones is not None
        assert list(zones) == ["peek"]

    def test_a_guarded_zone_is_declared_but_not_standalone(self, root: Path) -> None:
        page_file = write_page(root, "photo", PAGE_HEAD, body=guarded_zone_body("peek"))

        zones = own_zones(page_file)

        assert zones is not None
        assert not zones["peek"].standalone

    def test_a_template_that_fails_to_compose_has_no_zones(self, root: Path) -> None:
        page_file = write_page(root, "photo", PAGE_HEAD, body="{% if %}")

        assert own_zones(page_file) is None
