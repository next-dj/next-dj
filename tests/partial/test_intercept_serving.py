from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import resolve

from next.forms.uid import ORIGIN_FIELD_NAME
from next.intercept import Intercept, intercepts
from next.pages.errors import PageModuleImportError
from next.partial import Patches
from next.partial.errors import ForeignPageNotAuthorizedError
from next.partial.shaping.targets import _served_target
from next.partial.signals import intercept_access_denied
from next.testing import NextClient, capture_signals, envelope_of
from tests.support import partial_meta, routed, write_page
from tests.support.access import CONTEXT_CALLS, PAGE_HEAD, sign_in
from tests.support.cases.intercepts import zone_body


pytestmark = pytest.mark.django_db

_PROBE = (
    "from next.pages import context\n"
    "from tests.support.access import note_call\n\n\n"
    '@context("probe")\n'
    "def probe() -> str:\n"
    '    return note_call("probe")\n\n\n'
    '@context("side_probe", zone="side")\n'
    "def side_probe() -> str:\n"
    '    return note_call("side_probe")\n\n\n'
    "@page.metadata\n"
    "def head(probe: str) -> dict[str, str]:\n"
    '    return {"title": f"title {probe}"}\n'
)
_PEEK_AND_SIDE = zone_body("peek") + '{% zone "side" %}{{ side_probe }}{% endzone %}'


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


def _site(root: Path, *, intercept_source: str = "", peek: str | None = None) -> None:
    write_page(root, "photo/[int:pk]", PAGE_HEAD, body="<p>photo</p>")
    write_page(
        root,
        "(.)photo/[int:pk]",
        PAGE_HEAD + intercept_source,
        body=zone_body("peek") if peek is None else peek,
    )


class TestOneContextPerZoneGet:
    """A zone GET served by an intercept builds the context of the page once."""

    def test_a_metadata_callable_reuses_the_zone_context(self, root: Path) -> None:
        _site(root, intercept_source=_PROBE, peek=_PEEK_AND_SIDE)
        CONTEXT_CALLS.clear()

        with routed(root):
            response = NextClient().get_zones("/photo/3/", "peek")

        assert CONTEXT_CALLS == ["probe"]
        (head,) = [op for op in envelope_of(response).ops if op["op"] == "meta"]
        assert head["title"] == "title probe"

    def test_the_context_of_an_unrequested_zone_never_runs(self, root: Path) -> None:
        _site(root, intercept_source=_PROBE, peek=_PEEK_AND_SIDE)
        CONTEXT_CALLS.clear()

        with routed(root):
            NextClient().get_zones("/photo/3/", "peek")

        assert "side_probe" not in CONTEXT_CALLS


class TestDenialAnnounced:
    """An intercept refusal on a zone GET sends `intercept_access_denied`."""

    @pytest.mark.parametrize(
        ("rule", "user", "status", "reason"),
        [
            ("page.permission_required('auth.add_user')\n", "visitor", 403, "raised"),
            ("page.login_required()\n", None, 302, "redirect"),
        ],
        ids=["denied", "login"],
    )
    def test_the_signal_names_the_pages_and_the_answer(
        self, root: Path, rule: str, user: str | None, status: int, reason: str
    ) -> None:
        _site(root, intercept_source=rule)
        client = NextClient()
        if user is not None:
            sign_in(client, user)

        with routed(root), capture_signals(intercept_access_denied) as recorded:
            response = client.get_zones("/photo/3/", "peek")

        assert response.status_code == status
        [event] = recorded.events
        assert event.sender is Intercept
        assert event.kwargs["file_path"].parent.name == "[int:pk]"
        assert event.kwargs["file_path"].parent.parent.name == "(.)photo"
        assert event.kwargs["target_file"].parent.parent.name == "photo"
        assert event.kwargs["request"].path == "/photo/3/"
        assert (event.kwargs["status"], event.kwargs["reason"]) == (status, reason)

    def test_an_admitted_request_sends_nothing(self, root: Path) -> None:
        _site(root)

        with routed(root), capture_signals(intercept_access_denied) as recorded:
            response = NextClient().get_zones("/photo/3/", "peek")

        assert response.status_code == 200
        assert recorded.events == []


def _origin_post(zones: str | None):
    request = RequestFactory().post(
        "/_next/form/x/",
        data={ORIGIN_FIELD_NAME: "/photo/3/"},
        **partial_meta(zones=zones),
    )
    request.user = AnonymousUser()
    return request


class TestMorphPicksOverTheRequestZones:
    """A morph serves from the page the whole zone set of the request picked."""

    def test_a_morph_uses_the_intercept_the_zone_set_picked(self, root: Path) -> None:
        target = write_page(root, "photo/[int:pk]", PAGE_HEAD, body="<p>photo</p>")
        narrow = write_page(root, "narrow", PAGE_HEAD, body=zone_body("peek"))
        wide = write_page(root, "wide", PAGE_HEAD, body=zone_body("peek", "extra"))

        with routed(root):
            resolve("/photo/3/")
            intercepts.publish(
                root, [Intercept(target, narrow, "(.)"), Intercept(target, wide, "(.)")]
            )
            patches = Patches(_origin_post("peek,extra"))
            served = patches._served_page("peek")
            envelope = patches.morph_zone("peek").envelope()

        assert served == wide
        assert envelope.ops[0].html == '<div data-next-zone="peek">peek</div>'
        assert len(patches._render_contexts) == 1

    def test_a_zone_the_intercept_lacks_renders_from_the_origin(
        self, root: Path
    ) -> None:
        target = write_page(root, "photo/[int:pk]", PAGE_HEAD, body=zone_body("count"))
        modal = write_page(root, "modal", PAGE_HEAD, body=zone_body("peek"))

        with routed(root):
            resolve("/photo/3/")
            intercepts.publish(root, [Intercept(target, modal, "(.)")])
            patches = Patches(_origin_post("peek"))
            envelope = patches.morph_zone("count").envelope()

        assert envelope.ops[0].html == '<div data-next-zone="count">count</div>'

    def test_a_request_naming_no_zone_picks_nothing(self, root: Path) -> None:
        _site(root)

        with routed(root):
            resolve("/photo/3/")
            assert Patches(_origin_post(None))._served_page("peek") is None


class TestRefusingInterceptOnTheActionPath:
    """A refusal of the intercept is a 403 of the action path, a broken one a 500."""

    def test_a_refusing_intercept_is_a_403(self, root: Path) -> None:
        _site(root, intercept_source="page.login_required()\n")
        target = root / "photo" / "[int:pk]" / "page.py"
        request = RequestFactory().post("/x/", **partial_meta(zones="peek"))
        request.user = AnonymousUser()

        with routed(root):
            resolve("/photo/3/")
            with pytest.raises(PermissionDenied) as caught:
                _served_target(request, target, {"pk": 3})

        assert isinstance(caught.value.__cause__, ForeignPageNotAuthorizedError)

    def test_a_broken_intercept_chain_raises_its_import_failure(
        self, root: Path
    ) -> None:
        _site(root, intercept_source="raise RuntimeError('boom')\n")
        target = root / "photo" / "[int:pk]" / "page.py"
        request = RequestFactory().post("/x/", **partial_meta(zones="peek"))
        request.user = AnonymousUser()

        with routed(root):
            resolve("/photo/3/")
            with pytest.raises(PageModuleImportError):
                _served_target(request, target, {"pk": 3})
