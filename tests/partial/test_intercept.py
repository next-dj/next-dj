from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.test import RequestFactory, override_settings
from django.urls import resolve

from next.intercept import Intercept, intercepts
from next.pages import page
from next.pages.errors import PageModuleImportError
from next.partial import ForeignPageNotAuthorizedError, intercept as partial_intercept
from next.partial.intercept import (
    admit,
    pick_intercept,
    refuse_access,
    refuse_intercept,
    resolve_served_page,
)
from tests.support import routed, write_page
from tests.support.access import PAGE_HEAD, login_required_middleware, page_source
from tests.support.cases.intercepts import PICK_CASES, PickCase, zone_body


pytestmark = pytest.mark.django_db


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


@contextmanager
def _mounted(root: Path, url: str = "/photo/3/") -> Generator[None, None, None]:
    """Route `root` and build its patterns, which publishes the intercepts."""
    with routed(root):
        resolve(url)
        yield


def _page(root: Path, trail: str, body: str, source: str = PAGE_HEAD) -> Path:
    return write_page(root, trail, source, body=body)


def _rule(target: Path, file: Path) -> Intercept:
    return Intercept(target, file, "(.)")


def _request(codenames: tuple[str, ...] | None = None):
    """Return a GET by a user holding `codenames`, anonymous for `None`."""
    request = RequestFactory().get("/photo/3/")
    if codenames is None:
        request.user = AnonymousUser()
        return request
    user = get_user_model().objects.create_user("holder")
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    request.user = user
    return request


class TestPickIntercept:
    """The rule serving a zone is chosen from the declared zones, never a render."""

    @pytest.mark.parametrize("case", PICK_CASES, ids=lambda case: case.id)
    def test_the_rule_the_zones_select(self, root: Path, case: PickCase) -> None:
        target = _page(root, "photo", case.target)
        files = [_page(root, f"rule{i}", body) for i, body in enumerate(case.rules)]
        intercepts.publish(root, [_rule(target, file) for file in files])

        picked = pick_intercept(target, case.zones)

        expected = None if case.winner is None else files[case.winner]
        assert (picked.file if picked else None) == expected

    def test_a_miss_reads_no_template(
        self, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = _page(root, "photo", "")
        other = _page(root, "other", zone_body("a"))
        intercepts.publish(root, [_rule(other, other)])
        reads: list[Path] = []
        monkeypatch.setattr(page, "composed_template_for", reads.append)

        assert pick_intercept(target, ("a",)) is None
        assert reads == []

    def test_no_zone_asked_reads_no_template(
        self, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = _page(root, "photo", "")
        rule = _page(root, "rule", zone_body("a"))
        intercepts.publish(root, [_rule(target, rule)])
        reads: list[Path] = []
        monkeypatch.setattr(page, "composed_template_for", reads.append)

        assert pick_intercept(target, ()) is None
        assert reads == []


class TestResolveServedPage:
    """The page serving the zones comes back only after its own rule admits."""

    @pytest.fixture()
    def site(self, root: Path) -> tuple[Path, Path]:
        target = _page(root, "photo/[int:pk]", "<p>photo</p>")
        rule = _page(
            root,
            "(.)photo/[int:pk]",
            zone_body("peek"),
            f'{PAGE_HEAD}page.permission_required("auth.add_user")\n',
        )
        return target, rule

    def test_a_miss_serves_the_target(self, root: Path, site) -> None:
        target, _rule_file = site

        with _mounted(root):
            served = resolve_served_page(target, ("ghost",), _request(), {})

        assert served == target

    def test_an_admitted_request_is_served_by_the_intercept(
        self, root: Path, site
    ) -> None:
        target, rule = site

        with _mounted(root):
            served = resolve_served_page(
                target, ("peek",), _request(("add_user",)), {"pk": 3}
            )

        assert served == rule

    @pytest.mark.parametrize(
        ("codenames", "status"),
        [pytest.param(None, 302, id="anonymous"), pytest.param((), 403, id="visitor")],
    )
    def test_a_refusal_is_raised_never_a_fallback(
        self, root: Path, site, codenames, status
    ) -> None:
        target, rule = site

        with _mounted(root), pytest.raises(ForeignPageNotAuthorizedError) as caught:
            resolve_served_page(target, ("peek",), _request(codenames), {"pk": 3})

        assert (caught.value.page_path, caught.value.status_code) == (rule, status)


class TestRefuseAccess:
    """A page owes the request what its own view would, a failed import included."""

    def test_an_open_page_admits(self, root: Path) -> None:
        page_file = _page(root, "open", "x", page_source())

        with _mounted(root, "/open/"):
            assert refuse_access(page_file, _request(), {}) is None

    def test_a_holder_of_the_permission_admits(self, root: Path) -> None:
        page_file = _page(
            root, "shut", "x", f'{PAGE_HEAD}page.permission_required("auth.add_user")\n'
        )

        with _mounted(root, "/shut/"):
            assert refuse_access(page_file, _request(("add_user",)), {}) is None

    def test_a_broken_page_raises_its_import_failure(self, root: Path) -> None:
        page_file = _page(
            root, "broken", "x", f"{PAGE_HEAD}raise RuntimeError('boom')\n"
        )

        with _mounted(root, "/broken/"), pytest.raises(PageModuleImportError):
            refuse_access(page_file, _request(), {})


class TestRefuseIntercept:
    """An intercept is refused as its routed page would be, its body never run."""

    _BROKEN = f"{PAGE_HEAD}raise RuntimeError('boom')\n"

    @pytest.mark.parametrize("debug", [False, True], ids=["production", "debug"])
    def test_a_broken_intercept_raises_its_import_failure(
        self, root: Path, debug
    ) -> None:
        page_file = _page(root, "broken", "x", self._BROKEN)

        with (
            _mounted(root, "/broken/"),
            override_settings(DEBUG=debug),
            pytest.raises(PageModuleImportError),
        ):
            refuse_intercept(page_file, _request(()), {})

    def test_a_broken_intercept_sends_an_anonymous_user_to_login_first(
        self, root: Path
    ) -> None:
        page_file = _page(root, "broken", "x", self._BROKEN)

        with (
            login_required_middleware(),
            _mounted(root, "/broken/"),
            pytest.raises(ForeignPageNotAuthorizedError) as caught,
        ):
            refuse_intercept(page_file, _request(), {})

        assert caught.value.status_code == 302

    def test_a_denial_is_a_403(self, root: Path) -> None:
        page_file = _page(
            root, "shut", "x", f'{PAGE_HEAD}page.permission_required("auth.add_user")\n'
        )

        with (
            _mounted(root, "/shut/"),
            pytest.raises(ForeignPageNotAuthorizedError) as caught,
        ):
            refuse_intercept(page_file, _request(()), {})

        assert caught.value.status_code == 403


class TestGrantsWithinOneRequest:
    """A grant kept on the request spares only the same page, kwargs and user."""

    @pytest.fixture()
    def calls(self, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
        seen: list[Path] = []
        original = partial_intercept.refusal

        def spy(page_path, *args, **kwargs):
            seen.append(page_path)
            return original(page_path, *args, **kwargs)

        monkeypatch.setattr(partial_intercept, "refusal", spy)
        return seen

    def test_the_same_check_runs_once(self, root: Path, calls: list[Path]) -> None:
        page_file = _page(root, "open/[int:pk]", "x", page_source())
        request = _request()

        with _mounted(root, "/open/3/"):
            refuse_access(page_file, request, {"pk": 3})
            refuse_access(page_file, request, {"pk": 3})

        assert calls == [page_file]

    def test_the_zone_get_and_the_raising_paths_share_one_grant(
        self, root: Path, calls: list[Path]
    ) -> None:
        page_file = _page(root, "open", "x", page_source())
        request = _request()

        with _mounted(root, "/open/"):
            assert admit(page_file, request, {}, {}) is None
            refuse_intercept(page_file, request, {})
            refuse_access(page_file, request, {})

        assert calls == [page_file]

    def test_other_kwargs_are_checked_again(
        self, root: Path, calls: list[Path]
    ) -> None:
        page_file = _page(root, "open/[int:pk]", "x", page_source())
        request = _request()

        with _mounted(root, "/open/3/"):
            refuse_access(page_file, request, {"pk": 3})
            refuse_access(page_file, request, {"pk": 4})

        assert calls == [page_file, page_file]

    def test_another_page_is_checked_again(self, root: Path, calls: list[Path]) -> None:
        first = _page(root, "one", "x", page_source())
        second = _page(root, "two", "x", page_source())
        request = _request()

        with _mounted(root, "/one/"):
            refuse_access(first, request, {})
            refuse_access(second, request, {})

        assert calls == [first, second]

    def test_a_user_switched_mid_request_is_checked_again(
        self, root: Path, calls: list[Path]
    ) -> None:
        page_file = _page(
            root, "shut", "x", f'{PAGE_HEAD}page.permission_required("auth.add_user")\n'
        )
        request = _request(("add_user",))

        with _mounted(root, "/shut/"):
            refuse_access(page_file, request, {})
            request.user = AnonymousUser()
            with pytest.raises(ForeignPageNotAuthorizedError):
                refuse_access(page_file, request, {})

        assert calls == [page_file, page_file]

    def test_a_refusal_is_never_remembered(self, root: Path, calls: list[Path]) -> None:
        page_file = _page(
            root, "shut", "x", f'{PAGE_HEAD}page.permission_required("auth.add_user")\n'
        )
        request = _request(())

        with _mounted(root, "/shut/"):
            for _ in range(2):
                with pytest.raises(ForeignPageNotAuthorizedError):
                    refuse_access(page_file, request, {})

        assert calls == [page_file, page_file]

    def test_another_request_of_the_same_user_is_checked_again(
        self, root: Path, calls: list[Path]
    ) -> None:
        page_file = _page(root, "open", "x", page_source())
        first = _request()
        second = RequestFactory().get("/photo/3/")
        second.user = first.user

        with _mounted(root, "/open/"):
            refuse_access(page_file, first, {})
            refuse_access(page_file, second, {})

        assert calls == [page_file, page_file]
