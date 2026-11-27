import json
import os
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import pytest
from django.conf.urls.i18n import i18n_patterns
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, override_settings
from django.test.utils import override_script_prefix
from django.urls import (
    Resolver404,
    URLPattern,
    URLResolver,
    clear_url_caches,
    include,
    path,
    re_path,
    resolve,
)
from django.urls.converters import REGISTERED_CONVERTERS, get_converters
from django.utils import translation
from django.utils.translation import get_language_from_path

from next.urls import PageBackend, route_manager
from next.urls.scope import (
    LONGEST,
    OTHER_ROW,
    PAGE_ROW,
    Stranded,
    regex_head,
    route_rows,
    route_scope,
    scope_url,
    scope_view,
    stranded_pages,
)
from tests.support import (
    file_router_config_entry,
    installed_apps,
    write_app,
    write_page,
    write_urlconf,
)
from tests.support.access import COMPONENTS


GOLDEN = Path(__file__).parent / "golden" / "scope.json"
_UPDATE = os.environ.get("GOLDEN_UPDATE") == "1"
_PREFIX = "/app/"
_COMMON = "django.middleware.common.CommonMiddleware"
_PAGES = (
    "",
    "about",
    "blog/[slug]",
    "blog/[int:year]/archive",
    "docs/[[rest]]",
    "items/[uuid:key]",
    "tags/[slug:tag]",
    "(marketing)/pricing",
    "café",
)


def plain(request: HttpRequest, **kwargs: object) -> HttpResponse:
    return HttpResponse(b"plain")


class _PageView:
    """A view the URLconf names directly, marked as a page as the file router marks one."""

    next_page_path = Path("intl/page.py")

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return HttpResponse(b"page")


page_view = _PageView()


class GuessBackend(PageBackend):
    """Routes a plain view it cannot tell from a page."""

    def __init__(self, config: object) -> None:
        """Keep the entry, which names nothing more."""
        self.config = config

    def generate_urls(self) -> list[URLPattern | URLResolver]:
        return [path("guessed/", plain)]


class RefusingBackend(GuessBackend):
    """Routes a plain view and says it is no page."""

    def generate_urls(self) -> list[URLPattern | URLResolver]:
        return [path("refused/", plain)]

    def serves_page(self, pattern: URLPattern) -> bool | None:
        return False


class _Year:
    """A converter of its own whose regex the table carries."""

    regex = "[0-9]{4}"

    def to_python(self, value: str) -> int:
        return int(value)

    def to_url(self, value: int) -> str:
        return str(value)


class _DigitYear(_Year):
    """A converter of its own whose Unicode digit class the table cannot carry."""

    regex = r"\d{4}"


@pytest.fixture(autouse=True)
def converters() -> Iterator[None]:
    get_converters.cache_clear()
    with patch.dict(REGISTERED_CONVERTERS, {"yyyy": _Year(), "dyear": _DigitYear()}):
        yield
    get_converters.cache_clear()


def _urlconf() -> types.ModuleType:
    module = types.ModuleType("scope_urls")
    module.urlpatterns = [
        path("login/", plain),
        path("blog/feed/", plain),
        path("admin/", include([path("", plain), path("<path:rest>", plain)])),
        re_path(r"^archive/(?P<n>\d+)/$", plain),
        re_path(r"^(?P<free>[a-z]{2})x/$", plain),
        path("news/rss", plain),
        path("news/<slug:s>/", page_view),
        path("feed", plain),
        path("feed/", page_view),
        re_path(r"^posts/(?P<s>[a-z]+)/$", page_view),
        path("posts/5/like/", plain),
        path("tenant/", include([_language_include()])),
        path("shelf/", include([path("<slug:s>/", page_view), path("about", plain)])),
        path("cal/<yyyy:y>/", page_view),
        path("cal/logout/", plain),
        path("year/<dyear:y>/", page_view),
        re_path(r"^old/(?P<n>\d+)/$", page_view),
        path("files/<path:p>/", include([path("view/", page_view)])),
        path("", include("next.urls")),
        *i18n_patterns(path("intl/", page_view), prefix_default_language=False),
    ]
    return module


def _language_include() -> URLResolver:
    return re_path(
        r"^(?P<lang>en|de)/", include([path("a/", page_view), path("logout/", plain)])
    )


@pytest.fixture()
def site(tmp_path: Path) -> Iterator[None]:
    for trail in _PAGES:
        write_page(tmp_path, trail, body="<p>x</p>")
    backends = [
        file_router_config_entry(pages_dir=tmp_path),
        {"BACKEND": f"{__name__}.GuessBackend"},
        {"BACKEND": f"{__name__}.RefusingBackend"},
    ]
    framework: dict[str, Any] = {
        "PAGE_BACKENDS": backends,
        "COMPONENT_BACKENDS": COMPONENTS,
    }
    with (
        override_settings(
            ROOT_URLCONF=_urlconf(),
            NEXT_FRAMEWORK=framework,
            LANGUAGES=[("en", "English"), ("de", "German")],
            LANGUAGE_CODE="en",
            MIDDLEWARE=["django.middleware.common.CommonMiddleware"],
            APPEND_SLASH=True,
        ),
        override_script_prefix(_PREFIX),
    ):
        yield
    clear_url_caches()


def _cases() -> list[dict[str, Any]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def _lands_on_page(path: str, *, slash: bool = True) -> bool:
    try:
        match = resolve(path)
    except Resolver404:
        if not slash or path.endswith("/"):
            return False
        return _lands_on_page(f"{path}/", slash=False)
    return getattr(match.func, "next_page_path", None) is not None


def _resolves_to_page(href: str) -> bool:
    raw = unquote(urlsplit(href).path)
    if not raw.startswith(_PREFIX):
        return False
    rest = "/" + raw.removeprefix(_PREFIX)
    language = get_language_from_path(rest) or "en"
    with translation.override(language):
        try:
            match = resolve(rest)
        except Resolver404:
            return False
    return getattr(match.func, "next_page_path", None) is not None


class TestGolden:
    """The table and its cases are the fixture the client suite reads as well."""

    def test_the_table_is_the_golden_one(self, site: None) -> None:
        table = json.loads(route_scope().body)
        if _UPDATE:
            held = json.loads(GOLDEN.read_text(encoding="utf-8"))
            held["table"] = table
            GOLDEN.write_text(json.dumps(held, indent=2) + "\n", encoding="utf-8")
        assert table == json.loads(GOLDEN.read_text(encoding="utf-8"))["table"]

    @pytest.mark.parametrize("case", _cases(), ids=lambda case: case["href"])
    def test_the_table_owns_each_case_as_written(
        self, site: None, case: dict[str, Any]
    ) -> None:
        raw = unquote(urlsplit(case["href"]).path)
        owned = raw.startswith(_PREFIX) and route_scope().owns(
            raw.removeprefix(_PREFIX)
        )
        assert owned is case["owns"]

    @pytest.mark.parametrize("case", _cases(), ids=lambda case: case["href"])
    def test_every_page_of_the_urlconf_is_owned(
        self, site: None, case: dict[str, Any]
    ) -> None:
        page = _resolves_to_page(case["href"])
        assert page is case["page"]
        assert not page or case["owns"] or case.get("document") is True


class TestRows:
    """Each route reads as a row the client regex and the server regex agree on."""

    def test_rows_open_with_their_mark(self, site: None) -> None:
        rows = json.loads(route_scope().body)["routes"]
        assert {row[0] for row in rows} == {PAGE_ROW, OTHER_ROW}

    def test_a_route_no_page_overlaps_is_pruned(self, site: None) -> None:
        heads = {head for row in route_scope().rows for head in row.heads}
        assert "login/" not in heads
        assert "blog/feed/" in heads

    def test_the_url_patterns_name_each_page_from_the_root(self, site: None) -> None:
        patterns = route_scope().patterns(page=True)
        assert "/app/blog/{:p0}/" in patterns
        assert "/app/docs/*/" in patterns
        assert "/app/{(de)/}?intl/" in patterns
        assert "/app/posts/*" in patterns

    def test_the_others_come_from_every_row_by_their_starts(self, site: None) -> None:
        others = route_scope().patterns(page=False)
        assert "/app/blog/feed/" in others
        assert "/app/login/" not in others
        assert "/app/archive/*" not in others

    @pytest.mark.parametrize(
        ("patterns", "others"),
        [
            pytest.param(
                [path("blog/feed/", plain), path("blog/<int:y>/", page_view)],
                ["/blog/feed/"],
                id="pruned-row",
            ),
            pytest.param(
                [re_path(r"^p/[a-z]+$", page_view), path("p/feed/", plain)],
                ["/p/feed/", "/p/feed"],
                id="appended-slash",
            ),
        ],
    )
    def test_speculation_leaves_each_route_a_page_pattern_reaches(
        self, patterns: list[URLPattern], others: list[str]
    ) -> None:
        module = types.ModuleType("scope_speculation_urls")
        module.urlpatterns = patterns
        settled = {"MIDDLEWARE": [_COMMON], "APPEND_SLASH": True}
        with override_settings(ROOT_URLCONF=module, **settled):
            assert route_scope().patterns(page=False) == others
        clear_url_caches()

    def test_a_literal_page_keeps_a_route_that_matches_it(self, tmp_path: Path) -> None:
        write_page(tmp_path, "help", body="<p>x</p>")
        module = types.ModuleType("scope_literal_urls")
        module.urlpatterns = [path("<str:any>/", plain), path("", include("next.urls"))]
        framework = {
            "PAGE_BACKENDS": [file_router_config_entry(pages_dir=tmp_path)],
            "COMPONENT_BACKENDS": COMPONENTS,
        }
        with override_settings(ROOT_URLCONF=module, NEXT_FRAMEWORK=framework):
            table = route_scope()
            assert not table.owns("help/")
            assert table.patterns(page=False) == ["/{:p0}/"]
        clear_url_caches()

    def test_a_free_include_with_a_page_loads_it_as_a_document(self) -> None:
        module = types.ModuleType("scope_free_urls")
        module.urlpatterns = [
            re_path(r"^t/(?P<tenant>\w+)/", include([path("x/", page_view)])),
            re_path(r"^(?P<tenant>\w+)/", include([path("y/", plain)])),
            path("p/", page_view),
        ]
        with override_settings(ROOT_URLCONF=module):
            rows = route_scope().rows
            assert [(row.source, row.page) for row in rows] == [
                ("", False),
                ("p/$", True),
            ]
            assert not route_scope().owns("t/acme/x/")
            assert not route_scope().owns("p/")
            assert stranded_pages() == [
                Stranded(r"^t/(?P<tenant>\w+)/x/"),
                Stranded("p/", by=r"^(?P<tenant>\w+)/"),
            ]
        clear_url_caches()

    def test_a_nested_include_under_a_free_one_names_its_pages(self) -> None:
        module = types.ModuleType("scope_nested_urls")
        inner = include([path("y/", include([path("z/", page_view)]))])
        module.urlpatterns = [re_path(r"^n/\w+/", inner)]
        with override_settings(ROOT_URLCONF=module):
            assert stranded_pages() == [Stranded(r"^n/\w+/y/z/")]
        clear_url_caches()

    def test_a_path_with_no_slash_is_not_retried_without_the_middleware(
        self, site: None
    ) -> None:
        with override_settings(MIDDLEWARE=[]):
            assert route_scope().owns("about/")
            assert not route_scope().owns("about")

    def test_a_path_past_the_longest_is_never_owned(self, site: None) -> None:
        room = LONGEST - len(_PREFIX) - len("docs//")
        assert route_scope().owns(f"docs/{'a' * room}/")
        assert not route_scope().owns(f"docs/{'a' * (room + 1)}/")

    @pytest.mark.parametrize(
        "char", ["\n", "\r", "\x00", "\x1f", "\x7f", "\x85", "\u2028", "\u2029"]
    )
    def test_a_path_with_a_control_character_is_never_owned(
        self, site: None, char: str
    ) -> None:
        assert not route_scope().owns(f"blog/a{char}b/")


class TestLanguages:
    """A language prefix reads as the prefixes the languages of the site give it."""

    @pytest.mark.parametrize(
        ("languages", "prefix", "source", "glob"),
        [
            pytest.param(
                [("en", "English"), ("de", "German")],
                {"prefix_default_language": True},
                "(?:de/|en/)x/$",
                "/(de|en)/x/",
                id="every-language-named",
            ),
            pytest.param(
                [("en", "English")],
                {"prefix_default_language": False},
                "x/$",
                "/x/",
                id="one-language-unnamed",
            ),
        ],
    )
    def test_the_prefix_names_each_language(
        self,
        languages: list[tuple[str, str]],
        prefix: dict[str, bool],
        source: str,
        glob: str,
    ) -> None:
        module = types.ModuleType("scope_language_urls")
        module.urlpatterns = i18n_patterns(path("x/", page_view), **prefix)
        with override_settings(
            ROOT_URLCONF=module, LANGUAGES=languages, LANGUAGE_CODE="en"
        ):
            (row,) = route_scope().rows
            assert row.source == source
            assert route_scope().patterns(page=True) == [glob]
        clear_url_caches()


class TestTrees:
    """A `pages()` mount and a route of two converters read as the page rows they are."""

    def test_a_mounted_tree_lists_its_pages_under_its_prefix(
        self, tmp_path: Path
    ) -> None:
        write_app(tmp_path, "audit", {"pages": ("log/[int:entry]/[slug:part]",)})
        urlconf = write_urlconf(
            tmp_path, "scope_mount_urls", 'path("org/<int:org>/audit/", pages("audit"))'
        )
        with installed_apps(tmp_path, "audit", urlconf=urlconf):
            table = route_scope()
            assert table.owns("org/7/audit/log/2/intro/")
            assert not table.owns("org/x/audit/log/2/intro/")
        clear_url_caches()

    @pytest.mark.parametrize(
        ("route", "source"),
        [
            pytest.param("n/<yyyy:n>/", "n/(?:[0-9]{4})/$", id="carried"),
            pytest.param("n/<dyear:n>/", "n/", id="unicode-class"),
        ],
    )
    def test_a_converter_of_its_own_is_carried_once_translated(
        self, route: str, source: str
    ) -> None:
        module = types.ModuleType("scope_converter_urls")
        module.urlpatterns = [path(route, page_view)]
        with override_settings(ROOT_URLCONF=module):
            (row,) = route_rows()
        clear_url_caches()
        assert row.source == source


def _confs() -> dict[str, list[URLPattern | URLResolver]]:
    return {
        "slash-before": [path("blog/rss", plain), path("blog/<slug:s>/", page_view)],
        "slash-twin": [path("feed", plain), path("feed/", page_view)],
        "slash-after": [path("<slug:s>/", page_view), path("about", plain)],
        "regex-page": [
            re_path(r"^blog/(?P<s>[a-z]+)/$", page_view),
            path("blog/5/like/", plain),
        ],
        "free-include": [
            re_path(
                r"^(?P<lang>en|de)/",
                include([path("a/", page_view), path("logout/", plain)]),
            ),
            path("api/x/", plain),
            path("api/<slug:s>/", page_view),
        ],
        "own-converter": [path("<yyyy:y>/", page_view), path("logout/", plain)],
        "digit-converter": [path("<dyear:y>/", page_view), path("logout/", plain)],
        "free-regex": [
            re_path(r"^blog/\d+/$", plain),
            path("blog/<slug:s>/", page_view),
        ],
        "unanchored": [
            re_path(r"x/", plain),
            path("ax/", page_view),
            path("b/", page_view),
        ],
        "open-regex": [re_path(r"^open/", page_view), path("open/x/", plain)],
        "tail-prefix": [
            path("files/<path:p>/", include([path("view/", page_view)])),
            path("files/x/", page_view),
        ],
        "bounded-prefix": [
            path(
                "org/<int:org>/",
                include([path("edit/", plain), path("<slug:s>/", page_view)]),
            )
        ],
        "unbounded-prefix": [
            path("org/<int:org>", include([path("5/", page_view)])),
            path("org/<slug:s>/", page_view),
        ],
        "three-tails": [
            path("t/<path:a>/<path:b>/<path:c>/x/", page_view),
            path("t/<path:a>/<path:b>/", page_view),
        ],
        "slug-prefix": [
            path("u/<slug:s>-", include([path("x/", page_view)])),
            path("u/<slug:s>/", plain),
        ],
    }


_OWNED: dict[str, tuple[str, ...]] = {
    "slash-before": ("blog/5/", "blog/abc/", "blog/rss/", "blog/x", "blog/x/"),
    "slash-twin": ("feed/",),
    "slash-after": (
        "about/",
        "a/",
        "ax/",
        "b/",
        "bx/",
        "blog/",
        "feed",
        "feed/",
        "logout/",
        "open/",
        "x/",
        "2024/",
        "2024",
    ),
    "regex-page": ("blog/abc/", "blog/rss", "blog/rss/", "blog/x", "blog/x/"),
    "free-include": (),
    "own-converter": ("2024/", "2024"),
    "digit-converter": (),
    "free-regex": (),
    "unanchored": (),
    "open-regex": ("open/", "open/x/", "open/y"),
    "tail-prefix": (),
    "bounded-prefix": ("org/1/x/",),
    "unbounded-prefix": (),
    "slug-prefix": (),
    "three-tails": (),
}
_PROBES = (
    "",
    "about",
    "about/",
    "api/x/",
    "api/y/",
    "a/",
    "ax/",
    "b/",
    "bx/",
    "blog/",
    "blog/5/",
    "blog/5/like/",
    "blog/abc/",
    "blog/rss",
    "blog/rss/",
    "blog/x",
    "blog/x/",
    "de/a/",
    "en/a/",
    "en/logout/",
    "feed",
    "feed/",
    "files/a/b/view/",
    "files/x/",
    "files/x/view/",
    "logout/",
    "open/",
    "open/x/",
    "open/y",
    "org/1/",
    "org/1/edit/",
    "org/1/x/",
    "org/15/",
    "org/x/",
    "u/a-x/",
    "u/a-b-x/",
    "u/a/",
    "x/",
    "t/a/b/",
    "t/a/b/c/x/",
    "2024/",
    "2024",
    "٢٠٢٤/",
)


class TestOwnership:
    """A path the table owns lands on a page, as Django resolves it."""

    @pytest.mark.parametrize("name", sorted(_OWNED))
    def test_an_owned_path_resolves_to_a_page(self, name: str) -> None:
        module = types.ModuleType(f"scope_{name}_urls")
        module.urlpatterns = _confs()[name]
        settled = {"MIDDLEWARE": [_COMMON], "APPEND_SLASH": True}
        with override_settings(ROOT_URLCONF=module, **settled):
            table = route_scope()
            owned = [probe for probe in _PROBES if table.owns(probe)]
            wrong = [probe for probe in owned if not _lands_on_page(f"/{probe}")]
        clear_url_caches()
        assert wrong == []
        assert tuple(owned) == _OWNED[name]


class TestEndpoint:
    """The table is served under its digest, cached for good only at that digest."""

    def test_the_url_names_the_digest(self, site: None) -> None:
        assert scope_url() == f"/app/_next/routes/{route_scope().digest}/"

    def test_no_url_without_the_framework_urls(self) -> None:
        module = types.ModuleType("scope_bare_urls")
        module.urlpatterns = [path("x/", plain)]
        with override_settings(ROOT_URLCONF=module):
            assert scope_url() is None
        clear_url_caches()

    def test_the_current_digest_is_immutable(self, site: None) -> None:
        found = route_scope()
        response = scope_view(RequestFactory().get("/"), found.digest)
        assert response.content == found.body
        assert response["Content-Type"] == "application/json"
        assert "immutable" in response["Cache-Control"]

    def test_another_digest_gets_the_current_table_revalidated(
        self, site: None
    ) -> None:
        response = scope_view(RequestFactory().get("/"), "00000000")
        assert response.content == route_scope().body
        assert response["Cache-Control"] == "no-cache"

    def test_the_view_is_routed(self, site: None) -> None:
        digest = route_scope().digest
        match = resolve(f"/_next/routes/{digest}/")
        assert match.func is scope_view

    def test_a_routes_reload_moves_the_digest(self, site: None, tmp_path: Path) -> None:
        before = route_scope().digest
        write_page(tmp_path, "fresh", body="<p>x</p>")
        route_manager.reload()
        after = route_scope()
        assert after.digest != before
        assert after.owns("fresh/")


class TestRegexHead:
    """The literal start of a pattern regex stops where the regex stops being literal."""

    @pytest.mark.parametrize(
        ("regex", "head", "rest"),
        [
            pytest.param(r"^a\.b/\Z", "a.b/", r"\Z", id="escaped"),
            pytest.param(r"\Aa/\Z", "a/", r"\Z", id="string-start"),
            pytest.param(r"^ab?c/$", "a", "b?c/$", id="quantified"),
            pytest.param(r"^a\-?b/$", "a", r"\-?b/$", id="quantified-escape"),
            pytest.param(r"^a/(?P<x>\d+)/$", "a/", r"(?P<x>\d+)/$", id="group"),
            pytest.param(r"^a/\d+/$", "a/", r"\d+/$", id="class-escape"),
            pytest.param(r"^a/$|^b/$", "", r"^a/$|^b/$", id="alternation"),
        ],
    )
    def test_the_head_and_the_rest(self, regex: str, head: str, rest: str) -> None:
        assert regex_head(regex) == (head, rest)


class TestBackendContract:
    """A backend tells a page from a plain view, or says it cannot tell."""

    def test_a_page_view_is_a_page(self) -> None:
        assert GuessBackend({}).serves_page(path("x/", page_view)) is True

    def test_a_plain_view_is_unknown(self) -> None:
        assert GuessBackend({}).serves_page(path("x/", plain)) is None
