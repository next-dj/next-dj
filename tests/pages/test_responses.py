import logging
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.template import engines
from django.template.response import SimpleTemplateResponse
from django.test import Client, RequestFactory, override_settings
from django.utils.functional import SimpleLazyObject

from next.csrf import token_deferred
from next.pages import CacheDict, HeadersDict, page
from next.pages.loaders import load_page_module, module_generation, reset_module_memo
from next.pages.responses import (
    NO_STORE,
    CacheControl,
    ResponsePolicy,
    SharedCookies,
    cache_control,
    cache_problems,
    cookie_varies,
    finish_response,
    headers_problems,
    mark_personal_render,
    personal_render,
    prepare_page_render,
    response_policy,
    robots_tag,
    vary_on_cookie,
)
from next.partial.headers import VARY_HEADERS
from next.testing import NextClient
from tests.support import (
    CLOSED_SITE,
    bound_dependency,
    build_mock_http_request,
    build_page_request,
    consent_request,
    routed,
    touch_later,
    unified_view,
    write_page,
    write_page_chain,
)


UNSENDABLE = "must be ASCII text on one line, with no control character"


HEAD = "<html><head>{% metadata %}</head><body>{% template %}</body></html>"
RUNTIME = "<html><head></head><body>{% template %}{% collect_scripts %}</body></html>"
PARTIAL_VARY = ", ".join(VARY_HEADERS)
LAZY_PAGE = (
    "from django.contrib import messages\n"
    "from django.http import HttpRequest\n"
    "from django.template import engines\n"
    "from django.template.response import TemplateResponse\n\n"
    "cache = {cache}\n\n"
    "def render(request: HttpRequest):\n"
    "    {before}\n"
    "    template = engines['django'].from_string({source!r})\n"
    "    return TemplateResponse(request, template)\n"
)
SSE_PAGE = """
from next.partial import Patches, PatchEventStream


def render(request):
    return PatchEventStream(request, iter([Patches(request).event("tick")]))
"""


STATIC_COUNTED = """
from next.deps import Depends
from next.pages import page

template = "<p>{{ seen }}</p>"


@page.context("seen")
def seen(counter=Depends("counter")):
    return counter


def cache(counter=Depends("counter")):
    return 60
"""
CACHING_HEADERS = (
    "CDN-Cache-Control",
    "Cloudflare-CDN-Cache-Control",
    "Surrogate-Control",
    "Expires",
    "Age",
)
NONCE_MIDDLEWARE = [*settings.MIDDLEWARE, f"{__name__}.csp_nonce"]


def csp_nonce(get_response):
    def middleware(request):
        request.csp_nonce = "abc123"
        return get_response(request)

    return middleware


def lazy_csp_nonce(get_response):
    """Mint the nonce the way django-csp does, only once something reads it."""

    def middleware(request):
        def mint() -> str:
            request._csp_nonce = "lazy123"
            return "lazy123"

        request.csp_nonce = SimpleLazyObject(mint)
        return get_response(request)

    return middleware


def _tree(tmp_path: Path, *pages: tuple[str, str], layout: str = HEAD) -> Path:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text(layout)
    for trail, source in pages:
        write_page(root, trail, source, body="<p>x</p>")
    return root


def _get(root: Path, path: str = "/", **framework: object):
    with routed(root, **framework):
        return Client().get(path)


class TestCacheControl:
    """A `cache` value normalises to directives, a wrong shape to nothing."""

    def test_a_vary_name_no_header_carries_is_dropped(self) -> None:
        control = cache_control({"max_age": 60, "vary": ["Cookie\nX", "Accept", 3]})
        assert control is not None
        assert control.vary == ("Accept",)

    @pytest.mark.parametrize(
        ("value", "header"),
        [
            (60, "public, max-age=60"),
            (0, "public, max-age=0"),
            (False, "private, no-store"),
            (
                {
                    "public": True,
                    "max_age": 60,
                    "s_maxage": 300,
                    "stale_while_revalidate": 600,
                },
                "public, max-age=60, s-maxage=300, stale-while-revalidate=600",
            ),
            (
                {"immutable": True, "max_age": 31536000, "stale_if_error": 60},
                "immutable, max-age=31536000, stale-if-error=60",
            ),
            ({"public": True, "no_store": True, "s_maxage": 60}, "no-store"),
            (
                {"no_cache": True, "must_revalidate": True, "public": False},
                "no-cache, must-revalidate",
            ),
        ],
    )
    def test_each_form_sets_its_directives(self, value, header) -> None:
        control = cache_control(value)
        assert control is not None
        response = HttpResponse()
        control.apply(response)
        assert response["Cache-Control"] == header

    @pytest.mark.parametrize(
        "value", [None, True, -1, "60", 1.5, {}, {"max_age": -1, "vary": "Accept"}]
    )
    def test_a_wrong_shape_reads_as_no_declaration(self, value) -> None:
        assert cache_control(value) is None

    def test_a_declaration_naming_only_vary_sets_no_cache_control(self) -> None:
        control = cache_control({"vary": ["Accept"]})
        assert control is not None
        response = HttpResponse()
        control.apply(response)
        assert "Cache-Control" not in response
        assert response["Vary"] == "Accept"

    def test_vary_names_join_the_vary_header(self) -> None:
        control = cache_control({"max_age": 5, "vary": ["Accept", 3, "X-Tenant"]})
        assert control == CacheControl((("max_age", 5),), ("Accept", "X-Tenant"))
        response = HttpResponse()
        control.apply(response)
        assert response["Vary"] == "Accept, X-Tenant"

    @pytest.mark.parametrize(
        ("value", "shared", "seconds"),
        [
            (60, True, 60),
            ({"s_maxage": 300}, True, 300),
            ({"max_age": 60, "s_maxage": 300}, True, 60),
            ({"max_age": 60}, False, 60),
            (False, False, None),
        ],
    )
    def test_shared_and_seconds(self, value, shared, seconds) -> None:
        control = cache_control(value)
        assert control is not None
        assert (control.shared, control.seconds) == (shared, seconds)
        assert control.stores is (value is not False)

    def test_private_drops_every_shared_permission(self) -> None:
        control = cache_control({"public": True, "max_age": 60, "s_maxage": 300})
        assert control is not None
        private = control.private()
        assert private == CacheControl((("private", True), ("max_age", 60)))
        assert not private.shared
        assert NO_STORE.private() == NO_STORE

    def test_the_input_dicts_are_importable_from_the_pages_package(self) -> None:
        cache: CacheDict = {"public": True, "max_age": 60}
        headers: HeadersDict = {"X-Team": "web", "X-Old": None}
        assert cache_control(cache) is not None
        assert headers_problems(headers) == []


class TestProblems:
    """The checks read what keeps a declaration from being sent as written."""

    @pytest.mark.parametrize(
        "value",
        [None, False, 0, 60, {"public": True, "max_age": 60, "vary": ["Accept"]}],
    )
    def test_a_valid_cache_has_no_problem(self, value) -> None:
        assert cache_problems(value) == []

    def test_a_callable_is_valid_only_where_a_request_calls_it(self) -> None:
        assert cache_problems(lambda: 60) == []
        [problem] = cache_problems(lambda: 60, callable_allowed=False)
        assert problem.startswith("expected seconds as an int")

    @pytest.mark.parametrize(
        ("value", "problems"),
        [
            (-5, ["the age is negative"]),
            (True, ["expected seconds as an int, False, a CacheDict or a callable"]),
            ({"maxage": 1, "public": True}, ["unknown keys maxage"]),
            ({"public": "yes"}, ["public must be a bool"]),
            ({"max_age": -1}, ["max_age must be seconds as an int of 0 or more"]),
            ({"s_maxage": True}, ["s_maxage must be seconds as an int of 0 or more"]),
            ({"vary": "Accept"}, ["vary must be a list of header names"]),
            ({"vary": ["Bad Name"]}, ["vary must be a list of header names"]),
            ({"public": True, "no_store": True}, ["public contradicts no_store"]),
        ],
    )
    def test_each_cache_problem_is_named(self, value, problems) -> None:
        found = cache_problems(value)
        assert len(found) == len(problems)
        for text, expected in zip(found, problems, strict=True):
            assert text.startswith(expected)

    def test_valid_headers_have_no_problem(self) -> None:
        assert headers_problems(None) == []
        coop = {"Cross-Origin-Opener-Policy": "same-origin", "X-Gone": None}
        assert headers_problems(coop) == []

    @pytest.mark.parametrize(
        ("value", "problem"),
        [
            (["X-A"], "expected a mapping of header names to text or None"),
            ({"Bad Name": "x"}, "'Bad Name' is not a valid header name"),
            ({3: "x"}, "3 is not a valid header name"),
            ({"set-cookie": "a=b"}, "set-cookie is set by the framework"),
            ({"X-Robots-Tag": "noindex"}, "X-Robots-Tag is set by the framework"),
            (
                {"CDN-Cache-Control": "max-age=60"},
                "CDN-Cache-Control is set by cache, so declare the caching there",
            ),
            ({"expires": "0"}, "expires is set by cache, so declare the caching there"),
            (
                {"Content-Security-Policy": "frame-ancestors 'none'"},
                (
                    "Content-Security-Policy would replace the site policy, so "
                    "declare it through the CSP middleware"
                ),
            ),
            ({"X-A": "a\r\nb"}, f"the value of 'X-A' {UNSENDABLE}"),
            ({"X-A": "a\x00b"}, f"the value of 'X-A' {UNSENDABLE}"),
            ({"X-A": "x\u00e9"}, f"the value of 'X-A' {UNSENDABLE}"),
            ({"X-A": 3}, f"the value of 'X-A' {UNSENDABLE}"),
        ],
    )
    def test_each_header_problem_is_named(self, value, problem) -> None:
        assert headers_problems(value) == [problem]


class TestRobotsTag:
    """The header repeats the robots meta only where it keeps the page out."""

    @pytest.mark.parametrize(
        ("robots", "googlebot", "tag"),
        [
            (None, None, None),
            ("index, follow", None, None),
            ("noindex, follow", None, "noindex, follow"),
            ("NONE", "noindex", "NONE"),
            ("index, nofollow", None, "index, nofollow"),
            ("index", "noindex", "googlebot: noindex"),
        ],
    )
    def test_the_tag_follows_the_blocking_directives(
        self, robots, googlebot, tag
    ) -> None:
        assert robots_tag(robots, googlebot) == tag


class TestPageCache:
    """A page `cache` sets `Cache-Control` on its own response only."""

    def test_seconds_make_a_public_page_with_no_cookie(self, tmp_path) -> None:
        root = _tree(tmp_path, ("", 'template = "x"\ncache = 60\n'))
        response = _get(root)
        assert response["Cache-Control"] == "public, max-age=60"
        assert response["Vary"] == PARTIAL_VARY
        assert not response.cookies

    def test_the_landing_example_reads_whole(self, tmp_path) -> None:
        source = (
            'template = "x"\n'
            "cache = {'public': True, 'max_age': 60, 's_maxage': 300, "
            "'stale_while_revalidate': 600}\n"
            "headers = {'Cross-Origin-Opener-Policy': 'same-origin', "
            "'Content-Security-Policy': \"frame-ancestors 'none'\"}\n"
        )
        response = _get(_tree(tmp_path, ("", source)))
        assert response["Cache-Control"] == (
            "public, max-age=60, s-maxage=300, stale-while-revalidate=600"
        )
        assert response["Cross-Origin-Opener-Policy"] == "same-origin"
        assert "Content-Security-Policy" not in response

    def test_a_degraded_render_keeps_every_cache_away(self, tmp_path) -> None:
        # The failing callable may have held a noindex, so the page that lacks
        # it must not sit in a CDN for its declared lifetime.
        source = (
            "from next.pages import page\n"
            'template = "x"\n'
            "cache = 60\n"
            "@page.metadata\n"
            "def broken():\n"
            "    raise KeyError(0)\n"
        )
        response = _get(_tree(tmp_path, ("", source)))
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"

    def test_false_keeps_every_cache_away(self, tmp_path) -> None:
        response = _get(_tree(tmp_path, ("", 'template = "x"\ncache = False\n')))
        assert response["Cache-Control"] == "private, no-store"

    def test_the_cache_is_not_inherited(self, tmp_path) -> None:
        root = _tree(
            tmp_path,
            ("", 'template = "x"\ncache = 60\n'),
            ("child", 'template = "y"\n'),
        )
        response = _get(root, "/child/")
        assert "Cache-Control" not in response

    def test_a_callable_answers_per_request(self, tmp_path) -> None:
        source = (
            "from django.http import HttpRequest\n\n"
            'template = "x"\n\n'
            "def cache(request: HttpRequest):\n"
            "    return 60 if request.GET.get('public') else False\n"
        )
        root = _tree(tmp_path, ("", source))
        with routed(root):
            public = Client().get("/?public=1")
            private = Client().get("/")
        assert public["Cache-Control"] == "public, max-age=60"
        assert private["Cache-Control"] == "private, no-store"

    def test_a_callable_reads_the_url_kwargs(self, tmp_path) -> None:
        source = (
            "from next.urls import DUrl\n\n"
            'template = "x"\n\n'
            "def cache(slug: DUrl[str]):\n"
            "    return {'s_maxage': 300} if slug.startswith('paid') else None\n"
        )
        root = _tree(tmp_path, ("[slug]", source))
        with routed(root):
            paid = Client().get("/paid-fb/")
            plain = Client().get("/organic/")
        assert paid["Cache-Control"] == "s-maxage=300"
        assert "Cache-Control" not in plain

    @pytest.mark.parametrize(
        ("body", "fragment"),
        [
            ("raise RuntimeError('boom')", "raised"),
            ("return 'an hour'", "returned 'an hour'"),
            ("return {'max_age': -1}", "max_age must be seconds"),
        ],
        ids=["raises", "wrong_type", "wrong_value"],
    )
    def test_a_broken_callable_sends_no_store_and_logs_once(
        self, tmp_path, caplog, body, fragment
    ) -> None:
        source = f'template = "x"\n\ndef cache():\n    {body}\n'
        root = _tree(tmp_path, ("", source))
        with routed(root):
            responses = [Client().get("/") for _ in range(2)]
        assert [r.status_code for r in responses] == [200, 200]
        assert {r["Cache-Control"] for r in responses} == {"private, no-store"}
        [record] = [r for r in caplog.records if r.name == "next.pages.responses"]
        assert fragment in record.getMessage()
        assert "page.py" in record.getMessage()

    def test_a_raising_callable_raises_under_debug(self, tmp_path) -> None:
        source = 'template = "x"\n\ndef cache():\n    raise RuntimeError("boom")\n'
        root = _tree(tmp_path, ("", source))
        with (
            routed(root),
            override_settings(DEBUG=True),
            pytest.raises(RuntimeError) as raised,
        ):
            Client(raise_request_exception=True).get("/")
        assert "page.py raised" in raised.value.__notes__[0]

    def test_a_misshapen_answer_raises_under_debug(self, tmp_path) -> None:
        source = 'template = "x"\n\ndef cache():\n    return "soon"\n'
        root = _tree(tmp_path, ("", source))
        with (
            routed(root),
            override_settings(DEBUG=True),
            pytest.raises(ImproperlyConfigured, match=r"page\.py returned 'soon'"),
        ):
            Client(raise_request_exception=True).get("/")

    def test_an_intended_404_passes_through(self, tmp_path) -> None:
        source = (
            "from django.http import Http404\n\n"
            'template = "x"\n\n'
            "def cache():\n    raise Http404\n"
        )
        root = _tree(tmp_path, ("", source))
        assert _get(root).status_code == 404


class TestPageHeaders:
    """`headers` flow down the tree by name, the nearest page winning."""

    def test_headers_merge_by_name_without_regard_to_case(self, tmp_path) -> None:
        root = _tree(
            tmp_path,
            ("", "template = 'x'\nheaders = {'X-Team': 'web', 'X-Old': 'yes'}\n"),
            (
                "child",
                (
                    "template = 'y'\n"
                    "headers = {'x-team': 'child', 'x-old': None, 'X-New': 'on'}\n"
                ),
            ),
        )
        with routed(root):
            parent = Client().get("/")
            child = Client().get("/child/")
        assert (parent["X-Team"], parent["X-Old"]) == ("web", "yes")
        assert child["X-Team"] == "child"
        assert child["X-New"] == "on"
        assert "X-Old" not in child

    def test_a_forbidden_or_broken_header_is_left_out(self, tmp_path) -> None:
        source = (
            "template = 'x'\n"
            "headers = {'Set-Cookie': 'a=b', 'Bad Name': 'x', 'X-A': 'a\\nb', "
            "'X-Ok': 'yes'}\n"
        )
        response = _get(_tree(tmp_path, ("", source)))
        assert response["X-Ok"] == "yes"
        assert "X-A" not in response
        assert "a" not in response.cookies

    def test_a_caching_header_is_left_to_cache(self, tmp_path) -> None:
        declared = ", ".join(f"{name!r}: '600'" for name in CACHING_HEADERS)
        source = f"template = 'x'\ncache = 60\nheaders = {{{declared}, 'X-Ok': 'y'}}\n"
        response = _get(_tree(tmp_path, ("", source)))
        assert response["Cache-Control"] == "public, max-age=60"
        assert response["X-Ok"] == "y"
        for name in CACHING_HEADERS:
            assert name not in response

    def test_a_header_mapping_of_the_wrong_shape_is_ignored(self, tmp_path) -> None:
        response = _get(_tree(tmp_path, ("", "template = 'x'\nheaders = ['X-A']\n")))
        assert response.status_code == 200
        assert "X-A" not in response


class TestRenderResponse:
    """A response `render()` built keeps what it set, the page filling the gaps."""

    def _render(self, tmp_path: Path, body: str, extra: str = "") -> Path:
        source = (
            "from django.http import HttpResponse\n\n"
            "cache = 60\n"
            "headers = {'X-Team': 'web', 'X-Frame-Options': 'SAMEORIGIN'}\n"
            f"{extra}\n"
            "def render():\n"
            f"    {body}\n"
        )
        return _tree(tmp_path, ("", source))

    def test_headers_fill_only_the_gaps(self, tmp_path) -> None:
        root = self._render(
            tmp_path,
            "response = HttpResponse('x')\n"
            "    response['X-Team'] = 'mine'\n"
            "    return response",
        )
        response = _get(root)
        assert response["X-Team"] == "mine"
        assert response["X-Frame-Options"] == "SAMEORIGIN"
        assert response["Cache-Control"] == "public, max-age=60"

    def test_its_own_cache_control_wins(self, tmp_path) -> None:
        root = self._render(
            tmp_path,
            "response = HttpResponse('x')\n"
            "    response['Cache-Control'] = 'no-cache'\n"
            "    return response",
        )
        assert _get(root)["Cache-Control"] == "no-cache"

    def test_an_error_status_is_never_cached(self, tmp_path) -> None:
        root = self._render(tmp_path, "return HttpResponse('x', status=404)")
        assert "Cache-Control" not in _get(root)

    def test_a_cookie_it_set_takes_the_shared_cache_back(
        self, tmp_path, caplog
    ) -> None:
        root = self._render(
            tmp_path,
            "response = HttpResponse('x')\n"
            "    response.set_cookie('seen', '1')\n"
            "    return response",
        )
        with caplog.at_level(logging.WARNING, logger="next.pages.responses"):
            response = _get(root)
        assert response["Cache-Control"] == "private, max-age=60"
        assert "is sent with Cache-Control: private" in caplog.text

    def test_a_stream_keeps_its_cache_and_gets_the_site_robots(self, tmp_path) -> None:
        root = _tree(tmp_path, ("", "cache = 60\n" + SSE_PAGE))
        response = _get(root, **CLOSED_SITE)
        assert response["Cache-Control"] == "no-cache, no-transform"
        assert response["X-Robots-Tag"] == "noindex, nofollow"
        body = b"".join(response.streaming_content).decode()
        assert body.startswith("retry: ")
        assert '"ops":[{"op":"event","name":"tick","detail":{}}]' in body


class TestSharedDowngrade:
    """A shared page whose response is personal is sent private, never public."""

    def test_a_session_read_goes_private(self, tmp_path, caplog) -> None:
        source = (
            "from django.http import HttpRequest\n"
            "from next.pages import context\n\n"
            "template = '{{ who }}'\n"
            "cache = {'public': True, 's_maxage': 300}\n\n"
            "@context('who')\n"
            "def who(request: HttpRequest):\n"
            "    return request.session.get('name', 'anon')\n"
        )
        with caplog.at_level(logging.WARNING, logger="next.pages.responses"):
            response = _get(_tree(tmp_path, ("", source)))
        assert response["Cache-Control"] == "private"
        assert "Cookie" in response["Vary"]
        assert "is sent with Cache-Control: private" in caplog.text

    @pytest.mark.django_db()
    def test_a_session_write_sets_its_cookie_on_a_private_page(self, tmp_path) -> None:
        source = (
            "from django.http import HttpRequest\n"
            "from next.pages import context\n\n"
            "template = 'x'\n"
            "cache = 60\n\n"
            "@context('visit')\n"
            "def visit(request: HttpRequest):\n"
            "    request.session['seen'] = True\n"
            "    return 1\n"
        )
        response = _get(_tree(tmp_path, ("", source)))
        assert response.cookies["sessionid"].value
        assert response["Cache-Control"] == "private, max-age=60"

    def test_a_minted_csrf_token_goes_private(self, tmp_path) -> None:
        root = _tree(tmp_path, ("", "template = 'x'\ncache = 60\n"), layout=RUNTIME)
        response = _get(root, CSRF_DELIVERY="eager")
        assert response["Cache-Control"] == "private, max-age=60"
        assert response.cookies["csrftoken"].value

    def test_the_warning_is_logged_once_per_page(self, tmp_path, caplog) -> None:
        root = _tree(tmp_path, ("", "template = 'x'\ncache = 60\n"), layout=RUNTIME)
        with (
            routed(root, CSRF_DELIVERY="eager"),
            caplog.at_level(logging.WARNING, logger="next.pages.responses"),
        ):
            Client().get("/")
            Client().get("/")
        assert caplog.text.count("is sent with Cache-Control: private") == 1

    def test_a_cookie_set_after_the_view_takes_the_cache_back(self, tmp_path) -> None:
        file_path = write_page(tmp_path, "", "template = 'x'\n")
        request = RequestFactory().get("/")
        response = HttpResponse("x")
        policy = ResponsePolicy(cache_control({"public": True, "s_maxage": 60}))
        finish_response(response, policy, request, file_path)
        assert response["Cache-Control"] == "public, s-maxage=60"
        response.delete_cookie("old")
        assert response["Cache-Control"] == "private"
        response.set_cookie("new", "1")
        assert response["Cache-Control"] == "private"

    def test_a_request_with_credentials_goes_private(self, tmp_path) -> None:
        root = _tree(tmp_path, ("", "template = 'x'\ncache = 60\n"))
        with routed(root):
            anonymous = Client().get("/")
            signed = Client().get("/", HTTP_AUTHORIZATION="Bearer t0ken")
        assert anonymous["Cache-Control"] == "public, max-age=60"
        assert signed["Cache-Control"] == "private, max-age=60"

    def test_a_render_carrying_a_csp_nonce_goes_private(self, tmp_path) -> None:
        root = _tree(tmp_path, ("", "template = 'x'\ncache = 60\n"), layout=RUNTIME)
        with routed(root), override_settings(MIDDLEWARE=NONCE_MIDDLEWARE):
            response = Client().get("/")
        assert 'nonce="abc123"' in response.content.decode()
        assert response["Cache-Control"] == "private, max-age=60"

    def test_a_render_without_a_nonce_stays_shared(self, tmp_path) -> None:
        root = _tree(tmp_path, ("", "template = 'x'\ncache = 60\n"), layout=RUNTIME)
        with (
            routed(root, CSP_NONCE=False),
            override_settings(MIDDLEWARE=NONCE_MIDDLEWARE),
        ):
            response = Client().get("/")
        assert "nonce=" not in response.content.decode()
        assert response["Cache-Control"] == "public, max-age=60"

    def test_a_template_reading_the_nonce_goes_private(self, tmp_path) -> None:
        source = "template = '<i>{{ request.csp_nonce }}</i>'\ncache = 60\n"
        root = _tree(tmp_path, ("", source))
        middleware = [*settings.MIDDLEWARE, f"{__name__}.lazy_csp_nonce"]
        with routed(root, CSP_NONCE=False), override_settings(MIDDLEWARE=middleware):
            response = Client().get("/")
        assert "<i>lazy123</i>" in response.content.decode()
        assert response["Cache-Control"] == "private, max-age=60"

    def test_a_private_page_ignores_its_cookies(self) -> None:
        response = HttpResponse("x")
        response.set_cookie("a", "1")
        assert "Cache-Control" not in response


class TestLateCookies:
    """A `render()` response of any class turns private on the first cookie it gets."""

    def _root(
        self, tmp_path: Path, source: str, *, before: str = "pass", cache: object = 60
    ) -> Path:
        page_source = LAZY_PAGE.format(cache=cache, before=before, source=source)
        return _tree(tmp_path, ("", page_source))

    def test_a_token_minted_while_the_template_renders_goes_private(
        self, tmp_path
    ) -> None:
        root = self._root(tmp_path, "{% csrf_token %}")
        response = _get(root)
        assert response.cookies["csrftoken"].value
        assert response["Cache-Control"] == "private, max-age=60"

    def test_a_message_cookie_set_by_middleware_goes_private(self, tmp_path) -> None:
        root = self._root(tmp_path, "x", before="messages.info(request, 'hi')")
        response = _get(root)
        assert response.cookies["messages"].value
        assert response["Cache-Control"] == "private, max-age=60"

    def test_a_session_read_while_the_template_renders_goes_private(
        self, tmp_path, caplog
    ) -> None:
        root = self._root(tmp_path, "{{ request.session.name }}")
        with caplog.at_level(logging.WARNING, logger="next.pages.responses"):
            response = _get(root)
        assert response["Cache-Control"] == "private, max-age=60"
        assert "is sent with Cache-Control: private" in caplog.text

    def test_a_degrade_while_the_template_renders_keeps_caches_away(
        self, tmp_path
    ) -> None:
        # Contained after the cache is set, so the post-render hook replaces the cache.
        before = (
            "import logging, next.diagnostics as d; "
            "request.boom = type('B', (), {'__str__': lambda s: d.FailureLog("
            "logging.getLogger('t')).contain(RuntimeError('x'), 'k', 'm') or ''})()"
        )
        response = _get(self._root(tmp_path, "{{ request.boom }}", before=before))
        assert response["Cache-Control"] == "private, no-store"

    def test_a_template_of_nobody_in_particular_stays_public(self, tmp_path) -> None:
        response = _get(self._root(tmp_path, "<p>{{ 1 }}</p>"))
        assert response["Cache-Control"] == "public, max-age=60"
        assert not response.cookies

    def test_a_consent_read_while_the_template_renders_varies_and_goes_private(
        self, tmp_path
    ) -> None:
        file_path = write_page(tmp_path, "", "template = 'x'\n")
        request = RequestFactory().get("/")
        response = SimpleTemplateResponse(engines["django"].from_string("x"))
        finish_response(response, ResponsePolicy(cache_control(60)), request, file_path)
        vary_on_cookie(request)
        response.render()
        assert response["Cache-Control"] == "private, max-age=60"
        assert response["Vary"] == "Cookie"

    def test_a_lazy_private_page_varies_on_consent_after_its_render(
        self, tmp_path
    ) -> None:
        file_path = write_page(tmp_path, "", "template = 'x'\n")
        request = RequestFactory().get("/")
        response = SimpleTemplateResponse(engines["django"].from_string("x"))
        finish_response(response, ResponsePolicy(), request, file_path)
        vary_on_cookie(request)
        response.render()
        assert response["Vary"] == "Cookie"
        assert "Cache-Control" not in response

    def test_a_cached_copy_is_stored_without_its_guard(self, tmp_path) -> None:
        file_path = write_page(tmp_path, "", "template = 'x'\n")
        response = HttpResponse("x")
        policy = ResponsePolicy(cache_control(60))
        finish_response(response, policy, RequestFactory().get("/"), file_path)
        assert isinstance(response.cookies, SharedCookies)
        response.cookies["kept"] = "1"
        cache.set("next-test-shared", response)
        copy = cache.get("next-test-shared")
        cache.delete("next-test-shared")
        assert type(copy.cookies) is SimpleCookie
        assert copy.cookies["kept"].value == "1"
        assert copy["Cache-Control"] == "private, max-age=60"

    @pytest.mark.parametrize(
        "write",
        [
            lambda jar: jar.update({"sid": "2"}),
            lambda jar: jar.load("sid=2"),
            lambda jar: jar.load({"sid": "2"}),
        ],
        ids=["update", "load_text", "load_mapping"],
    )
    def test_every_write_to_the_jar_takes_it_private(self, tmp_path, write) -> None:
        file_path = write_page(tmp_path, "", "template = 'x'\n")
        response = HttpResponse("x")
        policy = ResponsePolicy(cache_control(60))
        finish_response(response, policy, RequestFactory().get("/"), file_path)
        write(response.cookies)
        assert response["Cache-Control"] == "private, max-age=60"

    def test_an_empty_write_keeps_the_page_shared(self, tmp_path) -> None:
        file_path = write_page(tmp_path, "", "template = 'x'\n")
        response = HttpResponse("x")
        policy = ResponsePolicy(cache_control(60))
        finish_response(response, policy, RequestFactory().get("/"), file_path)
        response.cookies.update({})
        assert response["Cache-Control"] == "public, max-age=60"


class TestCacheMethods:
    """Only a `GET` or a `HEAD` carries the cache a page declares."""

    def _root(self, tmp_path: Path) -> Path:
        source = (
            "from django.http import HttpResponse\n\n"
            "cache = {'public': True}\n\n"
            "def render():\n"
            "    return HttpResponse('x')\n"
        )
        return _tree(tmp_path, ("", source))

    def test_a_head_is_public(self, tmp_path) -> None:
        with routed(self._root(tmp_path)):
            response = Client().head("/")
        assert response["Cache-Control"] == "public"

    def test_a_post_carries_no_cache(self, tmp_path) -> None:
        with routed(self._root(tmp_path)):
            response = Client().post("/")
        assert response.status_code == 200
        assert "Cache-Control" not in response

    @pytest.mark.parametrize("method", ["get", "head"])
    def test_a_safe_method_on_a_static_page_is_public(self, tmp_path, method) -> None:
        with routed(_tree(tmp_path, ("", "template = 'x'\ncache = 60\n"))):
            response = getattr(Client(), method)("/")
        assert response["Cache-Control"] == "public, max-age=60"
        assert "Cookie" not in response["Vary"]
        assert "csrftoken" not in response.cookies

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete", "options"])
    def test_any_other_method_on_a_static_page_carries_no_cache(
        self, tmp_path, method
    ) -> None:
        with routed(_tree(tmp_path, ("", "template = 'x'\ncache = 60\n"))):
            response = getattr(Client(), method)("/")
        assert response.status_code == 200
        assert "Cache-Control" not in response
        assert "Cookie" in response["Vary"]

    def test_a_post_never_defers_the_token_or_marks_a_shared_render(
        self, tmp_path
    ) -> None:
        file_path = write_page(tmp_path, "", "template = 'x'\ncache = 60\n")
        request = RequestFactory().post("/")
        policy = response_policy(page, file_path, request, url_kwargs={})
        assert policy.cache is None
        assert not policy.shared


class TestZoneAndPatchResponses:
    """A zone envelope is never stored, whatever the page declares."""

    def test_a_zone_get_of_a_shared_page_is_private_no_store(self, tmp_path) -> None:
        root = _tree(
            tmp_path,
            ("", "template = '{% zone \"box\" %}<p>x</p>{% endzone %}'\ncache = 60\n"),
        )
        with routed(root, **CLOSED_SITE):
            response = NextClient().get_zones("/", "box")
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"
        assert response["X-Robots-Tag"] == "noindex, nofollow"

    def test_a_zone_get_of_a_dynamic_page_carries_the_site_robots(
        self, tmp_path
    ) -> None:
        root = _tree(tmp_path, ("", "def render():\n    return '<p>x</p>'\n"))
        with routed(root, **CLOSED_SITE):
            response = NextClient().get_zones("/", "box")
        assert response.status_code == 400
        assert response["X-Robots-Tag"] == "noindex, nofollow"

    def test_a_zone_answer_carries_the_page_headers(self, tmp_path) -> None:
        source = (
            "template = '{% zone \"box\" %}<p>x</p>{% endzone %}'\n"
            "cache = 60\n"
            "headers = {'X-Team': 'web'}\n"
        )
        with routed(_tree(tmp_path, ("", source))):
            response = NextClient().get_zones("/", "box")
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"
        assert response["X-Team"] == "web"

    def test_a_render_response_to_a_zone_get_is_never_stored(self, tmp_path) -> None:
        source = (
            "from django.http import HttpResponse\n\n"
            "cache = 60\n"
            "headers = {'X-Team': 'web'}\n\n"
            "def render():\n"
            "    return HttpResponse('x')\n"
        )
        with routed(_tree(tmp_path, ("", source))):
            response = NextClient().get_zones("/", "box")
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"
        assert response["X-Team"] == "web"

    def test_a_refused_zone_of_a_shared_page_is_never_stored(self, tmp_path) -> None:
        source = (
            "cache = 60\n"
            "headers = {'X-Team': 'web'}\n\n"
            "def render():\n"
            "    return '<p>x</p>'\n"
        )
        with routed(_tree(tmp_path, ("", source))):
            response = NextClient().get_zones("/", "box")
        assert response.status_code == 400
        assert response["Cache-Control"] == "private, no-store"
        assert response["X-Team"] == "web"


class TestOneDependencyCache:
    """A static page resolves its `cache` and its render through one cache."""

    def test_a_dependency_runs_once_for_the_cache_and_the_render(
        self, tmp_path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", STATIC_COUNTED)])
        calls: list[int] = []

        def counter() -> int:
            calls.append(1)
            return len(calls)

        with bound_dependency("counter", counter):
            response = unified_view(page, leaf)(build_page_request())
        assert calls == [1]
        assert response["Cache-Control"] == "public, max-age=60"
        assert "<p>1</p>" in response.content.decode()


class TestRobotsHeader:
    """`X-Robots-Tag` repeats a blocking robots meta, the site rule on top."""

    def test_the_rendered_head_is_repeated(self, tmp_path) -> None:
        source = (
            "template = 'x'\nmetadata = {'robots': {'index': False, 'follow': True}}\n"
        )
        response = _get(_tree(tmp_path, ("", source)))
        assert response["X-Robots-Tag"] == "noindex, follow"

    def test_a_page_without_a_head_falls_back_to_its_static_metadata(
        self, tmp_path
    ) -> None:
        source = "template = 'x'\nmetadata = {'robots': 'none'}\n"
        response = _get(_tree(tmp_path, ("", source), layout="{% template %}"))
        assert response["X-Robots-Tag"] == "none"

    def test_googlebot_alone_is_prefixed(self, tmp_path) -> None:
        source = (
            "template = 'x'\n"
            "metadata = {'robots': {'index': True, 'googlebot': 'noindex'}}\n"
        )
        response = _get(_tree(tmp_path, ("", source)))
        assert response["X-Robots-Tag"] == "googlebot: noindex"

    def test_an_indexable_page_carries_none(self, tmp_path) -> None:
        response = _get(_tree(tmp_path, ("", "template = 'x'\n")))
        assert "X-Robots-Tag" not in response

    def test_a_render_response_keeps_its_own_tag(self, tmp_path) -> None:
        source = (
            "from django.http import HttpResponse\n\n"
            "metadata = {'robots': 'noindex'}\n\n"
            "def render():\n"
            "    response = HttpResponse('x')\n"
            "    response['X-Robots-Tag'] = 'noarchive, noindex'\n"
            "    return response\n"
        )
        response = _get(_tree(tmp_path, ("", source)))
        assert response["X-Robots-Tag"] == "noarchive, noindex"

    def test_a_render_response_without_a_tag_gets_the_static_one(
        self, tmp_path
    ) -> None:
        source = (
            "from django.http import HttpResponse\n\n"
            "metadata = {'robots': 'noindex'}\n\n"
            "def render():\n"
            "    return HttpResponse('x')\n"
        )
        response = _get(_tree(tmp_path, ("", source)))
        assert response["X-Robots-Tag"] == "noindex"

    def test_a_closed_site_overrides_every_page(self, tmp_path) -> None:
        source = (
            "from django.http import HttpResponse\n\n"
            "def render():\n"
            "    response = HttpResponse('x')\n"
            "    response['X-Robots-Tag'] = 'all'\n"
            "    return response\n"
        )
        root = _tree(tmp_path, ("", "template = 'x'\n"), ("raw", source))
        with routed(root, **CLOSED_SITE):
            html = Client().get("/")
            raw = Client().get("/raw/")
        assert html["X-Robots-Tag"] == raw["X-Robots-Tag"] == "noindex, nofollow"


class TestCsrfDeferral:
    """A shared page keeps the CSRF token out of its HTML in `auto`."""

    @pytest.mark.parametrize(
        ("delivery", "cache", "deferred"),
        [
            ("auto", 60, True),
            ("auto", None, False),
            ("auto", {"max_age": 60}, False),
            ("eager", 60, False),
            ("lazy", None, True),
        ],
    )
    def test_the_mode_and_the_policy_decide(self, delivery, cache, deferred) -> None:
        request = RequestFactory().get("/")
        policy = ResponsePolicy(cache_control(cache))
        with override_settings(NEXT_FRAMEWORK={"CSRF_DELIVERY": delivery}):
            prepare_page_render(policy, request)
        assert token_deferred(request) is deferred

    def test_a_shared_page_ships_the_endpoint_and_sets_no_cookie(
        self, tmp_path
    ) -> None:
        root = _tree(tmp_path, ("", "template = 'x'\ncache = 60\n"), layout=RUNTIME)
        response = _get(root)
        body = response.content.decode()
        assert '"$csrf":{"header":"X-Csrftoken","url":"/_next/csrf/"}' in body
        assert response["Cache-Control"] == "public, max-age=60"
        assert not response.cookies

    def test_a_private_page_keeps_the_token_in_auto(self, tmp_path) -> None:
        root = _tree(tmp_path, ("", "template = 'x'\n"), layout=RUNTIME)
        response = _get(root)
        assert '"$csrf":{"header":"X-Csrftoken","token":' in response.content.decode()
        assert response.cookies["csrftoken"].value


class TestPolicyMemo:
    """The static part of a policy is read once per module load."""

    def test_the_policy_is_memoised_until_a_module_reload(self, tmp_path) -> None:
        root = _tree(tmp_path, ("", "template = 'x'\ncache = 60\n"))
        file_path = root / "page.py"
        request = RequestFactory().get("/")
        with routed(root):
            first = response_policy(page, file_path, request, url_kwargs={})
            assert response_policy(page, file_path, request, url_kwargs={}) is first
            reset_module_memo()
            assert response_policy(page, file_path, request, url_kwargs={}) == first
            assert response_policy(page, file_path, request, url_kwargs={}) is not first

    def test_a_load_elsewhere_keeps_the_policy_and_skips_the_stamps_after(
        self, tmp_path
    ) -> None:
        root = _tree(
            tmp_path,
            ("", "template = 'x'\ncache = 60\n"),
            ("other", "template = 'y'\n"),
        )
        file_path = root / "page.py"
        request = RequestFactory().get("/")
        with routed(root):
            first = response_policy(page, file_path, request, url_kwargs={})
            generation = module_generation()
            load_page_module(root / "other" / "page.py")
            assert module_generation() != generation
            assert response_policy(page, file_path, request, url_kwargs={}) is first
            with patch("next.pages.loaders.module_stamps", side_effect=AssertionError):
                assert response_policy(page, file_path, request, url_kwargs={}) is first

    def test_an_edit_loaded_elsewhere_rebuilds_the_policy(self, tmp_path) -> None:
        root = _tree(tmp_path, ("", "template = 'x'\ncache = 60\n"))
        file_path = root / "page.py"
        request = RequestFactory().get("/")
        with routed(root):
            assert response_policy(page, file_path, request, url_kwargs={}).shared
            file_path.write_text("template = 'x'\ncache = False\n")
            touch_later(file_path)
            load_page_module(file_path)
            policy = response_policy(page, file_path, request, url_kwargs={})
        assert policy.cache is NO_STORE

    def test_a_load_landing_mid_read_is_not_vouched_for(self, tmp_path) -> None:
        root = _tree(tmp_path, ("", "template = 'x'\ncache = 60\n"))
        file_path = root / "page.py"
        request = RequestFactory().get("/")
        edits: list[Path] = []

        def edited_mid_read(value: object) -> CacheControl | None:
            if not edits:
                edits.append(file_path)
                touch_later(file_path, "template = 'x'\ncache = False\n")
                load_page_module(file_path)
            return cache_control(value)

        with routed(root):
            with patch("next.pages.responses.cache_control", edited_mid_read):
                assert response_policy(page, file_path, request, url_kwargs={}).shared
            policy = response_policy(page, file_path, request, url_kwargs={})
        assert policy.cache is NO_STORE

    def test_a_watched_tree_reads_an_edit_at_once(self, tmp_path) -> None:
        root = _tree(tmp_path, ("", "template = 'x'\ncache = 60\n"))
        file_path = root / "page.py"
        request = RequestFactory().get("/")
        with routed(root), override_settings(DEBUG=True):
            assert response_policy(page, file_path, request, url_kwargs={}).shared
            file_path.write_text("template = 'x'\ncache = False\n")
            touch_later(file_path)
            policy = response_policy(page, file_path, request, url_kwargs={})
        assert policy.cache is NO_STORE

    def test_a_page_without_a_module_has_an_empty_policy(self, tmp_path) -> None:
        root = tmp_path / "pages"
        (root / "about").mkdir(parents=True)
        (root / "about" / "template.djx").write_text("x")
        request = RequestFactory().get("/")
        with routed(root):
            policy = response_policy(
                page, root / "about" / "page.py", request, url_kwargs={}
            )
        assert policy == ResponsePolicy()
        assert not policy.shared


class TestVary:
    """A render whose HTML follows the cookie marks its request."""

    def test_a_marked_request_varies(self) -> None:
        request = consent_request()
        assert not cookie_varies(request)
        vary_on_cookie(request)
        assert cookie_varies(request)

    def test_no_request_marks_nothing(self) -> None:
        vary_on_cookie(None)
        assert not cookie_varies(None)

    def test_a_stand_in_request_never_varies(self) -> None:
        assert not cookie_varies(build_mock_http_request())


class TestPersonalRender:
    """A render that put one visitor into its HTML marks its request."""

    def test_a_marked_request_is_personal(self) -> None:
        request = consent_request()
        assert not personal_render(request)
        mark_personal_render(request)
        assert personal_render(request)

    def test_no_request_marks_nothing(self) -> None:
        mark_personal_render(None)
        assert not personal_render(None)
