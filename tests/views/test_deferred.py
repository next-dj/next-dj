import re
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.urls import resolve

from next.components import registry as registry_module
from next.components.registry import DeferredComponents
from next.defer import stable_key
from next.pages import manager as manager_module
from next.pages.boundaries import shown_source
from next.suspense import assembly, boundary_rendered
from next.suspense.assembly import page_sink
from next.suspense.render import current_frame
from next.templatetags import components as component_tags
from next.testing import capture_signals, parallel_boundaries
from next.views import pages as view_pages
from tests.pages.checks.metadata.trees import COMPONENTS
from tests.support import routed, write_page


_SLOW = """
from next.components import component
from tests.views import test_deferred as host


@component.context("waited")
def waited() -> str:
    host.meet()
    return "yes"
"""

_MISSING = """
from django.http import Http404

from next.components import component


@component.context("value")
def value() -> str:
    raise Http404
"""

_BROKEN = """
from next.components import component


@component.context("value")
def value() -> str:
    raise ValueError("down")
"""

_PUBLIC = "x = 1\n"
_SHARED = 'cache = {"public": True, "max_age": 60}\n'
_THREE = (
    "<main>{% component 'slow' n=1 %}{% component 'slow' n=2 %}"
    "{% component 'slow' n=3 %}</main>"
)


def meet() -> None:
    """Let a job through at once, unless a test makes jobs meet."""


def _component(
    pages: Path,
    name: str,
    *,
    module: str | None = None,
    error: str | None = None,
    loading: str | None = "<p>loading</p>",
) -> None:
    folder = pages / "_components" / name
    folder.mkdir(parents=True)
    (folder / "component.djx").write_text("<p>{{ name }}{{ n }} {{ waited }}</p>")
    (folder / "component.css").write_text(".x {}")
    if module is not None:
        (folder / "component.py").write_text(module)
    if error is not None:
        (folder / "error.djx").write_text(error)
    if loading is not None:
        (folder / "loading.djx").write_text(loading)


@pytest.fixture()
def flag(monkeypatch: pytest.MonkeyPatch) -> DeferredComponents:
    """Track the deferred components of this test alone."""
    fresh = DeferredComponents()
    for module in (registry_module, manager_module, component_tags):
        monkeypatch.setattr(module, "deferred_components", fresh)
    return fresh


@pytest.fixture()
def pages(tmp_path: Path, flag: DeferredComponents) -> Path:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text(
        "<html><head>{% collect_styles %}</head><body>{% template %}</body></html>"
    )
    _component(root, "slow", module=_SLOW)
    return root


def _get(pages: Path, url: str, **extra: object) -> HttpResponse:
    with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
        return Client(raise_request_exception=False).get(url, **extra)


def _ready(response: HttpResponse) -> list[str]:
    return re.findall(r"<p>(\d) yes</p>", response.content.decode())


@pytest.mark.django_db()
class TestDeferredPage:
    """A page assembles every deferred component it holds before it answers."""

    @pytest.fixture(autouse=True)
    def _three(self, pages: Path) -> None:
        write_page(pages, "three", _PUBLIC, body=_THREE)

    def test_three_slow_jobs_run_side_by_side(
        self, pages: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _get(pages, "/three/")
        barrier = threading.Barrier(3, timeout=5)
        monkeypatch.setattr(sys.modules[__name__], "meet", barrier.wait)
        with parallel_boundaries():
            response = _get(pages, "/three/")
        html = response.content.decode()
        assert barrier.broken is False, "the three jobs never ran at once"
        assert response.status_code == 200
        assert _ready(response) == ["1", "2", "3"]
        assert html.count('data-next-state="ready"') == 3
        assert "<!--b-" not in html
        assert "slow.css" in html.split("</head>")[0]

    def test_the_page_sink_renders_the_shell(self, pages: Path) -> None:
        with mock.patch.object(
            assembly, "deferring", wraps=assembly.deferring
        ) as opened:
            response = _get(pages, "/three/")
        assert response.status_code == 200
        (sink, request), _ = opened.call_args
        assert sink is page_sink
        assert request.path == "/three/"

    def test_a_head_request_runs_no_job(self, pages: Path) -> None:
        with (
            capture_signals(boundary_rendered) as recorder,
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
        ):
            response = Client().head("/three/")
        assert response.status_code == 200
        assert len(recorder) == 0

    def test_a_head_shell_carries_no_marker(self, pages: Path) -> None:
        request = RequestFactory().head("/three/")
        request.user = AnonymousUser()
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            match = resolve("/three/")
            response = match.func(request, **match.kwargs)
        html = response.content.decode()
        assert html.count('aria-busy="true"') == 3
        assert "<!--b-" not in html

    def test_a_post_runs_its_jobs_on_the_request_thread(self, pages: Path) -> None:
        with (
            capture_signals(boundary_rendered) as recorder,
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
        ):
            response = Client().post("/three/")
        assert response.status_code == 200
        assert _ready(response) == ["1", "2", "3"]
        placed = {(str(e.kwargs["lane"]), str(e.kwargs["reason"])) for e in recorder}
        assert placed == {("request", "atomic")}

    def test_every_job_is_announced_with_its_request(self, pages: Path) -> None:
        with capture_signals(boundary_rendered) as recorder:
            _get(pages, "/three/")
        assert [event.kwargs["name"] for event in recorder] == ["slow"] * 3
        assert all(event.kwargs["request"] is not None for event in recorder)


@pytest.mark.django_db()
class TestDeferredOutcomes:
    """A failing, missing or shared component shapes the response it lands in."""

    def test_a_failing_component_shows_its_error_ui_and_degrades(
        self, pages: Path
    ) -> None:
        _component(pages, "broken", module=_BROKEN, error="<p>broken</p>")
        write_page(
            pages,
            "mixed",
            _PUBLIC,
            body="{% component 'slow' n=1 %}{% component 'broken' %}",
        )
        response = _get(pages, "/mixed/")
        html = response.content.decode()
        assert response.status_code == 200
        assert _ready(response) == ["1"]
        assert 'data-next-error="error"><p>broken</p>' in html
        assert response["Cache-Control"] == "private, no-store"

    def test_a_404_inside_a_job_answers_404(self, pages: Path) -> None:
        _component(pages, "gone", module=_MISSING)
        write_page(pages, "gone", _PUBLIC, body="{% component 'gone' %}")
        assert _get(pages, "/gone/").status_code == 404

    def test_a_shared_page_with_pool_jobs_stays_cacheable(self, pages: Path) -> None:
        write_page(pages, "shared", _PUBLIC + _SHARED, body=_THREE)
        with parallel_boundaries():
            response = _get(pages, "/shared/")
        assert response.status_code == 200
        assert response["Cache-Control"] == "public, max-age=60"
        assert "Cookie" not in response.get("Vary", "")

    def test_a_zone_get_assembles_its_jobs(self, pages: Path) -> None:
        write_page(
            pages,
            "zoned",
            _PUBLIC,
            body='{% zone "z" %}{% component "slow" n=7 %}{% endzone %}',
        )
        response = _get(pages, "/zoned/", HTTP_X_NEXT_REQUEST="1", HTTP_X_NEXT_ZONE="z")
        assert response.status_code == 200
        assert "<p>7 yes</p>" in response.content.decode()
        assert 'data-next-state=\\"ready\\"' in response.content.decode()


@pytest.mark.django_db()
class TestZeroCost:
    """A project without a deferred component never reaches the deferred machinery."""

    def test_a_plain_page_opens_no_block_and_no_thread(
        self, tmp_path: Path, flag: DeferredComponents
    ) -> None:
        root = tmp_path / "pages"
        write_page(root, "plain", _PUBLIC, body="<p>plain</p>")
        _get(root, "/plain/")
        before = set(threading.enumerate())
        with (
            mock.patch.object(view_pages, "page_deferring", side_effect=AssertionError),
            mock.patch.object(
                manager_module, "render_deferred", side_effect=AssertionError
            ),
        ):
            response = _get(root, "/plain/")
        assert response.status_code == 200
        assert flag.held is False
        assert set(threading.enumerate()) <= before


CALLS: list[tuple[str, str]] = []


def note(name: str) -> None:
    """Record that the callable `name` ran, in a job or in the shell."""
    CALLS.append((name, "job" if current_frame() is not None else "shell"))


def stall() -> None:
    """Hold a gated callable past the page timeout of a test."""
    time.sleep(0.5)


_SECTION = """
from next.pages import context
from tests.views import test_deferred as host


@context("section", inherit_context=True)
def section() -> str:
    host.note("section")
    return "S"
"""

_ITEM = """
from django.http import Http404, HttpRequest
from next import page
from next.pages import context
from tests.views import test_deferred as host

page.defer(timeout="200ms")


@context("a_item")
def a_item(request: HttpRequest) -> str:
    host.note("a_item")
    if request.GET.get("id") == "missing":
        raise Http404
    return "item"


@context("a_helper")
def a_helper() -> str:
    host.note("a_helper")
    return "helper"


@context("b_checked", guard=True)
def b_checked(a_helper: str) -> str:
    host.note("b_checked")
    return a_helper + "!"


@context("c_detail")
def c_detail(a_item: str) -> str:
    host.note("c_detail")
    return a_item + "-detail"


@context("relaxed", guard=False)
def relaxed(request: HttpRequest) -> str:
    host.note("relaxed")
    if request.GET.get("id") == "relaxed":
        raise Http404
    return "ok"


@context("state", serialize=True)
def state(request: HttpRequest) -> str:
    host.note("state")
    if request.GET.get("fail"):
        raise RuntimeError("down")
    if request.GET.get("slow"):
        host.stall()
    return "fine"
"""

_ITEM_BODY = (
    "<article>{{ a_item }} {{ b_checked }} {{ c_detail }} {{ relaxed }} {{ state }} "
    "{{ section }} {% component 'slow' n=2 %}"
    "{% zone 'z' %}<i>{{ c_detail }}</i>{% endzone %}</article>"
)

_ASYNC = """
from next.pages import context
from tests.views import test_deferred as host


@context("later")
async def later() -> str:
    host.note("later")
    return "L"
"""

_RENDERED = """
from next.pages import context
from tests.views import test_deferred as host


@context("later")
async def later() -> str:
    host.note("later")
    return "L"


async def render() -> str:
    return "<b>{{ later }}</b>"
"""

_TITLED = """
from next import page
from next.pages import context
from tests.views import test_deferred as host


@context("headline")
def headline() -> str:
    host.note("headline")
    return "H"


@context("body")
def body() -> str:
    host.note("body")
    return "B"


@page.metadata
def meta_title(headline: str) -> dict[str, str]:
    return {"title": headline}
"""

_NESTED = {
    "outer": """
from next.pages import context
from tests.views import test_deferred as host


@context("two", inherit_context=True)
def two() -> str:
    host.note("two")
    return "2"
""",
    "inner": """
from next.pages import context
from tests.views import test_deferred as host


@context("one", inherit_context=True)
def one(two: str) -> str:
    host.note("one")
    return "1+" + two
""",
    "leaf": """
from next.pages import context
from tests.views import test_deferred as host


@context("zero")
def zero(one: str) -> str:
    host.note("zero")
    return "0+" + one
""",
}


def _shop(pages: Path) -> Path:
    shop = pages / "shop"
    shop.mkdir()
    (shop / "page.py").write_text(_SECTION)
    (shop / "layout.djx").write_text(
        "<section>{% component 'slow' n=1 %}{% template %}</section>"
    )
    (shop / "loading.djx").write_text("<p>loading {{ section }}</p>")
    write_page(pages, "shop/item", _ITEM, body=_ITEM_BODY)
    return shop


def _calls(where: str) -> list[str]:
    return [name for name, place in CALLS if place == where]


@pytest.mark.django_db()
class TestGatedPage:
    """A page directory with a `loading.djx` defers the content below its slot."""

    @pytest.fixture(autouse=True)
    def _fresh(self) -> None:
        CALLS.clear()

    @pytest.fixture()
    def shop(self, pages: Path) -> Path:
        return _shop(pages)

    def test_the_body_runs_in_a_job_its_wrapper_settled_ready(self, shop: Path) -> None:
        response = _get(shop.parent, "/shop/item/")

        html = response.content.decode()
        key = stable_key(shown_source(shop / "loading.djx"), 0)
        assert response.status_code == 200
        assert f'data-next-boundary-key="{key}" data-next-state="ready">' in html
        assert "<article>item helper! item-detail ok fine S" in html
        assert "<!--b-" not in html

    def test_the_gated_callables_run_in_the_job_and_the_rest_in_the_shell(
        self, shop: Path
    ) -> None:
        _get(shop.parent, "/shop/item/")

        assert sorted(_calls("shell")) == ["a_helper", "a_item", "b_checked", "section"]
        assert sorted(_calls("job")) == ["c_detail", "relaxed", "state"]

    def test_a_component_in_the_body_and_one_in_the_layout_both_settle(
        self, shop: Path
    ) -> None:
        response = _get(shop.parent, "/shop/item/")

        assert _ready(response) == ["1", "2"]

    def test_a_guard_answers_404_before_any_job_runs(self, shop: Path) -> None:
        response = _get(shop.parent, "/shop/item/?id=missing")

        assert response.status_code == 404
        assert _calls("job") == []

    def test_an_intended_exception_of_the_job_reaches_the_request(
        self, shop: Path
    ) -> None:
        response = _get(shop.parent, "/shop/item/?id=relaxed")

        assert response.status_code == 404
        assert "relaxed" in _calls("job")

    def test_a_failing_body_without_an_error_file_answers_500(self, shop: Path) -> None:
        assert _get(shop.parent, "/shop/item/?fail=1").status_code == 500

    def test_a_failing_body_renders_the_error_file_at_500(self, shop: Path) -> None:
        (shop / "error.djx").write_text(
            "<p>failed {{ error.kind }} {{ section }} {{ c_detail|default:'clean' }}</p>"
        )

        response = _get(shop.parent, "/shop/item/?fail=1")

        html = response.content.decode()
        assert response.status_code == 500
        assert 'data-next-state="error" data-next-error="error"' in html
        assert "<p>failed error S clean</p>" in html
        assert "<p>1 yes</p>" in html

    def test_the_page_timeout_settles_through_the_error_file(self, shop: Path) -> None:
        (shop / "error.djx").write_text("<p>failed {{ error.kind }}</p>")

        with parallel_boundaries():
            response = _get(shop.parent, "/shop/item/?slow=1")

        assert 'data-next-error="timeout"><p>failed timeout</p>' in (
            response.content.decode()
        )

    def test_a_zone_get_renders_its_zone_with_the_gated_callables(
        self, shop: Path
    ) -> None:
        response = _get(
            shop.parent, "/shop/item/", HTTP_X_NEXT_REQUEST="1", HTTP_X_NEXT_ZONE="z"
        )

        assert response.status_code == 200
        assert "item-detail" in response.content.decode()
        assert "c_detail" in _calls("shell")
        assert _calls("job") == []

    def test_a_head_request_answers_the_shell_alone(self, shop: Path) -> None:
        with routed(shop.parent, COMPONENT_BACKENDS=COMPONENTS):
            response = Client().head("/shop/item/")

        assert response.status_code == 200
        assert _calls("job") == []

    def test_a_post_renders_the_body_in_place_of_its_loading_ui(
        self, shop: Path
    ) -> None:
        with routed(shop.parent, COMPONENT_BACKENDS=COMPONENTS):
            response = Client().post("/shop/item/")

        html = response.content.decode()
        assert response.status_code == 200
        assert "<article>item helper! item-detail ok fine S" in html
        assert 'aria-busy="true"' not in html


@pytest.mark.django_db()
class TestGatedVariants:
    """Async callables, nested boundaries and metadata reads keep the split."""

    @pytest.fixture(autouse=True)
    def _fresh(self) -> None:
        CALLS.clear()

    def test_an_async_gated_callable_runs_in_the_job(self, pages: Path) -> None:
        write_page(pages, "slowly", _ASYNC, body="<b>{{ later }}</b>")
        (pages / "slowly" / "loading.djx").write_text("<p>wait</p>")

        response = _get(pages, "/slowly/")

        assert 'data-next-state="ready"><b>L</b>' in response.content.decode()
        assert _calls("job") == ["later"]

    def test_each_loading_file_runs_the_callables_below_it(self, pages: Path) -> None:
        outer = pages / "outer"
        inner = outer / "inner"
        write_page(pages, "outer", _NESTED["outer"])
        write_page(outer, "inner", _NESTED["inner"])
        write_page(inner, "leaf", _NESTED["leaf"], body="<b>{{ zero }}</b>")
        (outer / "loading.djx").write_text("<p>outer</p>")
        (inner / "loading.djx").write_text("<p>inner</p>")
        (inner / "layout.djx").write_text("<div>{% template %}</div>")

        response = _get(pages, "/outer/inner/leaf/")

        html = response.content.decode()
        assert html.count('data-next-state="ready"') == 2
        assert re.search(r'ready"><div><div [^>]*ready"><b>0\+1\+2</b>', html)
        assert _calls("shell") == ["two"]
        assert _calls("job") == ["one", "zero"]

    def test_metadata_pulls_the_callable_it_reads_into_the_shell(
        self, pages: Path
    ) -> None:
        write_page(pages, "titled", _TITLED, body="<b>{{ body }}</b>")
        (pages / "titled" / "loading.djx").write_text("<p>wait</p>")

        _get(pages, "/titled/")

        assert _calls("shell") == ["headline"]
        assert _calls("job") == ["body"]

    def test_an_awaited_render_leaves_its_gated_callables_to_the_job(
        self, pages: Path
    ) -> None:
        write_page(pages, "rendered", _RENDERED)
        (pages / "rendered" / "loading.djx").write_text("<p>wait</p>")

        response = _get(pages, "/rendered/")

        assert 'data-next-state="ready"><b>L</b>' in response.content.decode()
        assert _calls("job") == ["later"]

    def test_a_guard_sees_what_every_callable_before_it_set(self, pages: Path) -> None:
        write_page(pages, "blocked", _BLOCKED, body="<b>{{ zz_guard }}</b>")
        (pages / "blocked" / "loading.djx").write_text("<p>wait</p>")

        response = _get(pages, "/blocked/")

        assert response.status_code == 404
        assert _calls("shell") == ["flagged", "zz_guard"]
        assert _calls("job") == []

    def test_an_inheritable_callable_feeds_the_layout_of_its_directory(
        self, pages: Path
    ) -> None:
        write_page(pages, "section", _NAV, body="<p>index {{ nav }}</p>")
        (pages / "section" / "layout.djx").write_text(
            '<nav>{{ nav|default:"EMPTY" }}</nav>{% template %}'
        )
        (pages / "section" / "loading.djx").write_text("<p>wait</p>")

        response = _get(pages, "/section/")

        html = response.content.decode()
        assert "<nav>NAV</nav>" in html
        assert "<p>index NAV</p>" in html
        assert _calls("shell") == ["nav"]

    def test_metadata_reading_a_keyless_value_pulls_the_keyless_callable(
        self, pages: Path
    ) -> None:
        write_page(pages, "keyless", _KEYLESS, body="<b>{{ body }}</b>")
        (pages / "keyless" / "loading.djx").write_text("<p>wait</p>")

        _get(pages, "/keyless/")

        assert _calls("shell") == ["extras"]
        assert _calls("job") == ["body"]

    def test_a_timeout_without_an_error_file_answers_500_privately(
        self, pages: Path
    ) -> None:
        _shop(pages)

        with parallel_boundaries():
            response = _get(pages, "/shop/item/?slow=1")

        assert response.status_code == 500
        assert "private" in response["Cache-Control"]
        assert 'data-next-error="timeout"' in response.content.decode()


_BLOCKED = """
from django.http import Http404, HttpRequest
from next.pages import context
from tests.views import test_deferred as host


@context("flagged")
def flagged(request: HttpRequest) -> str:
    host.note("flagged")
    request.blocked = True
    return "f"


@context("zz_guard")
def zz_guard(request: HttpRequest) -> str:
    host.note("zz_guard")
    if getattr(request, "blocked", False):
        raise Http404
    return "g"
"""

_NAV = """
from next.pages import context
from tests.views import test_deferred as host


@context("nav", inherit_context=True)
def nav() -> str:
    host.note("nav")
    return "NAV"
"""

_KEYLESS = """
from next import page
from next.pages import context
from tests.views import test_deferred as host


@context()
def extras() -> dict[str, str]:
    host.note("extras")
    return {"kl": "KL"}


@context("body")
def body() -> str:
    host.note("body")
    return "B"


@page.metadata
def meta_title(kl=None) -> dict[str, str]:
    return {"title": f"T-{kl}"}
"""


_NORMAL = re.compile(r'b-[0-9a-f]+-|"token":"[^"]*"')


@pytest.mark.django_db()
class TestFirstResponse:
    """The first response of a page with a `loading.djx` equals every later one."""

    @pytest.fixture(autouse=True)
    def _fresh(self) -> None:
        CALLS.clear()

    @pytest.fixture()
    def bare(self, tmp_path: Path) -> Path:
        root = tmp_path / "bare"
        write_page(root, "titled", _TITLED, body="<b>{{ body }}</b>")
        (root / "titled" / "loading.djx").write_text("<p>wait</p>")
        return root

    @pytest.mark.parametrize("method", ["get", "head"])
    def test_the_first_and_the_second_response_are_equal(
        self, bare: Path, flag: DeferredComponents, method: str
    ) -> None:
        with routed(bare):
            client = Client()
            first = getattr(client, method)("/titled/")
            jobs = _calls("job")
            second = getattr(client, method)("/titled/")

        assert first.status_code == second.status_code == 200
        assert _NORMAL.sub("", first.content.decode()) == _NORMAL.sub(
            "", second.content.decode()
        )
        assert jobs == ([] if method == "head" else ["body"])
        assert flag.held is False


_FAILING = """
from django.http import HttpRequest
from next.pages import context


def fails(request: HttpRequest, name: str) -> None:
    if request.GET.get("f") == name:
        raise RuntimeError(name)


def bomb(request: HttpRequest, name: str) -> object:
    def explode() -> str:
        if request.GET.get("t") == name:
            raise RuntimeError(name)
        return ""

    return explode
"""

_LEVELS = {
    "top": """

@context("top", inherit_context=True)
def top(request: HttpRequest) -> str:
    fails(request, "top")
    return "T"


@context("top_bomb", inherit_context=True)
def top_bomb(request: HttpRequest) -> object:
    return bomb(request, "top")
""",
    "m": """

@context("mid", inherit_context=True)
def mid(request: HttpRequest, top: str) -> str:
    fails(request, "mid")
    return top + "M"


@context("mid_bomb", inherit_context=True)
def mid_bomb(request: HttpRequest) -> object:
    return bomb(request, "mid")
""",
    "leaf": """

@context("leaf")
def leaf(request: HttpRequest, mid: str) -> str:
    fails(request, "leaf")
    return mid + "L"


@context("leaf_bomb")
def leaf_bomb(request: HttpRequest) -> object:
    return bomb(request, "leaf")
""",
}

_ERROR_UI = (
    '<e>{} {{{{ error.kind }}}} {{{{ top|default:"-" }}}} {{{{ mid|default:"-" }}}}</e>'
)


def _levels(pages: Path, name: str, loading: str) -> None:
    """Write a three-level tree under `name`, a `loading.djx` where `loading` says."""
    (pages / "error.djx").write_text("<e>root {{ error.kind }}</e>")
    top = pages / name
    write_page(pages, name, _FAILING + _LEVELS["top"])
    (top / "layout.djx").write_text("<s>{{ top }}{{ top_bomb }}|{% template %}</s>")
    (top / "error.djx").write_text(_ERROR_UI.format("top"))
    write_page(top, "m", _FAILING + _LEVELS["m"])
    (top / "m" / "layout.djx").write_text(
        "<m>{{ mid }}{{ mid_bomb }}|{% template %}</m>"
    )
    (top / "m" / "error.djx").write_text(_ERROR_UI.format("mid"))
    leaf = top / "m" / "leaf"
    write_page(
        top / "m",
        "leaf",
        _FAILING + _LEVELS["leaf"],
        body="<a>{{ leaf }}{{ leaf_bomb }}</a>",
    )
    (leaf / "error.djx").write_text(_ERROR_UI.format("leaf"))
    for directory, mark in ((top, "T"), (top / "m", "M"), (leaf, "L")):
        if mark in loading:
            (directory / "loading.djx").write_text(f"<p>{mark}</p>")


def _shown(response: HttpResponse) -> tuple[int, str]:
    html = response.content.decode()
    body = html.split("<body>", 1)[1].split("</body>", 1)[0].split("<script", 1)[0]
    return response.status_code, re.sub(r"</?div[^>]*>", "", body)


@pytest.mark.django_db()
class TestDeferredFailures:
    """A failure shows the same error UI in the same place with or without deferral."""

    @pytest.mark.parametrize("loading", ["T", "M", "L", "TML"])
    @pytest.mark.parametrize(
        "query", ["f=top", "f=mid", "f=leaf", "t=top", "t=mid", "t=leaf", "ok=1"]
    )
    def test_a_deferred_tree_fails_as_the_inline_one(
        self, pages: Path, loading: str, query: str
    ) -> None:
        _levels(pages, "inline", "")
        _levels(pages, "deferred", loading)

        inline = _shown(_get(pages, f"/inline/m/leaf/?{query}"))
        deferred = _shown(_get(pages, f"/deferred/m/leaf/?{query}"))

        assert deferred == inline
        assert ("<e>" in inline[1]) is (query != "ok=1")
