import logging
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from django.contrib.sitemaps import Sitemap
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import Http404
from django.test import Client, override_settings
from django.urls import NoReverseMatch

from next.pages.responses import cache_control
from next.seo import SitemapBackend
from next.seo.manager import seo_manager
from next.testing import parse_sitemap
from tests.seo.sources import CALLS
from tests.support import BASE, WITH_BASE, routed, write_tree


RAISING_RULES = """
from django.http import HttpRequest


def rules(request: HttpRequest):
    raise RuntimeError("rules down")
"""
RAISING_KWARGS = """
from next.seo import sitemap


@sitemap.items("posts/[slug]", kwargs=lambda row: row["missing"])
def posts():
    return [{"slug": "a"}]
"""
UNREVERSABLE = """
from next.seo import sitemap


@sitemap.items("posts/[slug]")
def posts():
    return [{"slug": "a/b"}]
"""
DEFAULT_ENTRY = {"BACKEND": "next.seo.PageTreeSitemapBackend"}


class Section(Sitemap):
    """One fixed URL, for a backend to serve."""

    def items(self) -> list[str]:
        """List the one URL."""
        return ["/news/"]

    def location(self, item: str) -> str:
        """Answer the URL as it is."""
        return item


class NewsBackend(SitemapBackend):
    """A backend serving one section, cached for two minutes."""

    def sections(self, request):
        """Answer the one section, counting the call."""
        CALLS.append("news")
        return {"news": Section()}

    def cache_control(self):
        """Ask for two minutes."""
        return cache_control(120)


class RaisingBackend(SitemapBackend):
    """A backend whose sections fail, as a database outage does."""

    def sections(self, request):
        """Fail."""
        msg = "sections down"
        raise RuntimeError(msg)


class RefusingBackend(SitemapBackend):
    """A backend that refuses the request on purpose."""

    def sections(self, request):
        """Refuse."""
        raise PermissionDenied


class MissingBackend(SitemapBackend):
    """A backend that answers 404 on purpose."""

    def sections(self, request):
        """Find nothing."""
        raise Http404


class BrokenServesBackend(RaisingBackend):
    """A backend whose `serves()` raises."""

    def serves(self):
        """Fail."""
        msg = "serves down"
        raise RuntimeError(msg)


class NotFoundServesBackend(RaisingBackend):
    """A backend whose `serves()` raises `Http404`, as `get_object_or_404` does."""

    def serves(self):
        """Find nothing."""
        raise Http404


def _backends(*names: str) -> dict[str, object]:
    entries = [{"BACKEND": f"tests.seo.test_failures.{name}"} for name in names]
    return {"SEO": {"SITEMAP_BACKENDS": entries}}


def _opened_by_header(request) -> bool:
    return request is not None and request.headers.get("X-Open") == "1"


def _raising_url(request) -> str:
    msg = "tenant lookup down"
    raise RuntimeError(msg)


@pytest.fixture(autouse=True)
def _fresh_calls() -> Iterator[None]:
    CALLS.clear()
    yield
    CALLS.clear()


def _views_records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "next.seo.views"]


class TestFailingSources:
    """Project code that raises answers 503 and logs once, and `DEBUG` re-raises."""

    @pytest.mark.parametrize(
        ("sources", "path", "named"),
        [
            ({"robots": RAISING_RULES}, "/robots.txt", "rules callable"),
            ({"sitemap": RAISING_KWARGS}, "/sitemap.xml", "the kwargs= callable"),
            ({"sitemap": UNREVERSABLE}, "/sitemap.xml", "reversing the trail"),
        ],
        ids=["rules", "kwargs", "reverse"],
    )
    def test_a_raising_source_answers_503_and_logs_once(
        self, tmp_path, caplog, sources, path, named
    ) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), **sources)
        with routed(root, **WITH_BASE), caplog.at_level(logging.ERROR):
            first = Client().get(path)
            second = Client().get(path)
        assert first.status_code == second.status_code == 503
        assert first["Retry-After"] == "300"
        assert first.content == b""
        [record] = _views_records(caplog)
        assert named in record.getMessage()

    @pytest.mark.parametrize(
        ("sources", "path", "raised", "named"),
        [
            ({"robots": RAISING_RULES}, "/robots.txt", RuntimeError, "rules"),
            ({"sitemap": RAISING_KWARGS}, "/sitemap.xml", KeyError, "kwargs= callable"),
            ({"sitemap": UNREVERSABLE}, "/sitemap.xml", NoReverseMatch, "'a/b'"),
        ],
        ids=["rules", "kwargs", "reverse"],
    )
    @override_settings(DEBUG=True)
    def test_debug_raises_naming_the_source(
        self, tmp_path, sources, path, raised, named
    ) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), **sources)
        with routed(root, **WITH_BASE), pytest.raises(raised) as caught:
            Client().get(path)
        notes = " ".join(caught.value.__notes__)
        assert "Raised" in notes
        assert "answers 503" in notes
        if raised is not NoReverseMatch:
            assert named in notes


class TestBackends:
    """A `SITEMAP_BACKENDS` entry serves over HTTP, and fails like every source."""

    def test_a_custom_backend_serves_with_its_cache_control(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages")
        with routed(root, **WITH_BASE, **_backends("NewsBackend")):
            first = Client().get("/sitemap.xml")
            second = Client().get("/sitemap.xml")
        assert first.status_code == 200
        assert [url.loc for url in parse_sitemap(first)] == [f"{BASE}/news/"]
        assert first["Cache-Control"] == "public, max-age=120"
        assert second.content == first.content
        assert CALLS == ["news"]

    def test_raising_sections_answer_503_uncached(self, tmp_path, caplog) -> None:
        root = write_tree(tmp_path / "pages", sitemap="cache = 60\n")
        backends = {"SITEMAP_BACKENDS": [DEFAULT_ENTRY, _entry("RaisingBackend")]}
        with routed(root, **WITH_BASE, SEO=backends), caplog.at_level(logging.ERROR):
            first = Client().get("/sitemap.xml")
            second = Client().get("/sitemap.xml")
        assert first.status_code == second.status_code == 503
        assert "Cache-Control" not in first
        [record] = _views_records(caplog)
        assert "RaisingBackend.sections()" in record.getMessage()

    @override_settings(DEBUG=True)
    def test_debug_raises_naming_the_backend(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages")
        with (
            routed(root, **WITH_BASE, **_backends("RaisingBackend")),
            pytest.raises(RuntimeError, match="sections down") as caught,
        ):
            Client().get("/sitemap.xml")
        assert (
            "Raised by tests.seo.test_failures.RaisingBackend.sections()."
            in caught.value.__notes__
        )

    def test_a_refusal_is_never_contained(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages")
        with routed(root, **WITH_BASE, **_backends("RefusingBackend")):
            response = Client().get("/sitemap.xml")
        assert response.status_code == 403

    def test_a_404_answers_the_plain_body(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages")
        with routed(root, **WITH_BASE, **_backends("MissingBackend")):
            response = Client().get("/sitemap.xml")
        assert response.status_code == 404
        assert response.content == b"Not found"

    def test_raising_serves_keeps_every_route(self, tmp_path, caplog) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "about"))
        with (
            routed(root, **WITH_BASE, **_backends("BrokenServesBackend")),
            caplog.at_level(logging.ERROR),
        ):
            page = Client().get("/about/")
            first = Client().get("/sitemap.xml")
            seo_manager.reset()
            second = Client().get("/sitemap.xml")
        assert page.status_code == 200
        assert first.status_code == second.status_code == 503
        served = [r for r in caplog.records if "serves() raised" in r.getMessage()]
        assert len(served) == 1

    @override_settings(DEBUG=False)
    def test_a_404_from_serves_keeps_every_route(self, tmp_path, caplog) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "about"))
        with (
            routed(root, **WITH_BASE, **_backends("NotFoundServesBackend")),
            caplog.at_level(logging.ERROR),
        ):
            home = Client().get("/")
            about = Client().get("/about/")
            sitemap = Client().get("/sitemap.xml")
        assert [home.status_code, about.status_code] == [200, 200]
        assert sitemap.status_code == 503
        assert any("serves() raised" in r.getMessage() for r in caplog.records)

    @override_settings(DEBUG=True)
    def test_debug_raises_from_serves_naming_the_backend(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages")
        with (
            routed(root, **WITH_BASE, **_backends("BrokenServesBackend")),
            pytest.raises(RuntimeError, match="serves down") as caught,
        ):
            Client().get("/sitemap.xml")
        assert caught.value.__notes__[0].startswith(
            "tests.seo.test_failures.BrokenServesBackend.serves() raised, so "
            "/sitemap.xml stays mounted"
        )

    def test_a_raising_robots_lookup_keeps_every_route(self, tmp_path, caplog) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "about"), robots="")
        with (
            routed(root, **WITH_BASE),
            patch.object(seo_manager, "robots_source", side_effect=OSError("disk")),
            caplog.at_level(logging.ERROR),
        ):
            page = Client().get("/about/")
            robots = Client().get("/robots.txt")
            seo_manager.reset()
            Client().get("/robots.txt")
        assert page.status_code == 200
        assert robots.status_code == 503
        routed_logs = [r for r in caplog.records if r.name == "next.seo.routes"]
        assert len(routed_logs) == 1
        assert "/robots.txt raised" in routed_logs[0].getMessage()


def _entry(name: str) -> dict[str, str]:
    return {"BACKEND": f"tests.seo.test_failures.{name}"}


class TestSiteUrl:
    """A callable `URL` that fails leaves no origin, so the SEO routes answer 503."""

    @pytest.mark.parametrize(
        ("sources", "path"),
        [({"sitemap": ""}, "/sitemap.xml"), ({"robots": ""}, "/robots.txt")],
        ids=["sitemap", "robots"],
    )
    def test_a_raising_url_answers_503_and_logs_once(
        self, tmp_path, caplog, sources, path
    ) -> None:
        root = write_tree(tmp_path / "pages", **sources)
        with (
            routed(root, SITE={"URL": _raising_url}),
            caplog.at_level(logging.ERROR, logger="next.site"),
        ):
            first = Client().get(path)
            second = Client().get(path)
        assert first.status_code == second.status_code == 503
        [record] = [r for r in caplog.records if r.name == "next.site.config"]
        assert "NEXT_FRAMEWORK['SITE']['URL']" in record.getMessage()

    @override_settings(DEBUG=True)
    def test_debug_raises_naming_the_setting(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with (
            routed(root, SITE={"URL": _raising_url}),
            pytest.raises(ImproperlyConfigured, match=r"\['URL'\] .*_raising_url"),
        ):
            Client().get("/sitemap.xml")


class TestIndexableCache:
    """A cached copy is keyed by the indexability of the request it answered."""

    def test_a_closed_request_never_reads_the_open_copy(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="cache = 60\n")
        site = {"URL": None, "INDEXABLE": _opened_by_header}
        with routed(root, SITE=site):
            opened = Client().get("/sitemap.xml", HTTP_X_OPEN="1")
            closed = Client().get("/sitemap.xml")
            again = Client().get("/sitemap.xml", HTTP_X_OPEN="1")
        assert opened.status_code == again.status_code == 200
        assert closed.status_code == 404
        assert "noindex" in closed["X-Robots-Tag"]

    @pytest.mark.parametrize(
        "source",
        ["", "cache = False\n", "cache = {'vary': ['Accept-Language']}\n"],
        ids=["no_cache", "no_store", "no_stored_age"],
    )
    def test_a_source_without_a_stored_copy_skips_the_indexability(
        self, tmp_path, source: str
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap=source)
        with (
            routed(root, SITE={"URL": None, "INDEXABLE": True}),
            patch("next.seo.views.site_indexable") as indexable,
        ):
            first = Client().get("/sitemap.xml")
            second = Client().get("/sitemap.xml")
        assert first.status_code == second.status_code == 200
        indexable.assert_not_called()
