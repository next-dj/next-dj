from pathlib import Path

import pytest
from django.contrib.sites.requests import RequestSite
from django.core.exceptions import DisallowedHost
from django.http import HttpResponse
from django.test import Client, RequestFactory, override_settings

from next.seo.origin import Origin, request_origin
from next.site import SiteOriginError
from next.testing import assert_metadata, override_next_settings, parse_sitemap
from tests.support import routed, write_metadata_tree, write_tree


ABOUT = ("about", "{'title': 'About', 'canonical': True}")


def _canonical_and_loc(root: Path, **extra: object) -> tuple[HttpResponse, str]:
    client = Client()
    with routed(root):
        page = client.get("/about/", **extra)
        sitemap = client.get("/sitemap.xml", **extra)
    [loc] = [url.loc for url in parse_sitemap(sitemap)]
    return page, loc


class TestRequestOrigin:
    """The site URL comes first, then the current site, then the request host."""

    def test_the_site_url_wins_over_the_request(self) -> None:
        request = RequestFactory().get("/", HTTP_HOST="testserver")
        with override_next_settings(SITE={"URL": "https://acme.example"}):
            assert request_origin(request) == Origin("https", "acme.example")
            assert request_origin(None) == Origin("https", "acme.example")

    def test_without_a_site_url_the_request_answers(self) -> None:
        request = RequestFactory().get("/", secure=True, HTTP_HOST="testserver")
        assert request_origin(request) == Origin("https", "testserver")

    def test_a_request_free_caller_without_a_site_url_raises(self) -> None:
        with pytest.raises(SiteOriginError):
            request_origin(None)

    @override_settings(ALLOWED_HOSTS=["acme.example"])
    def test_a_disallowed_host_raises_for_a_400(self) -> None:
        request = RequestFactory().get("/", HTTP_HOST="evil.example")
        with pytest.raises(DisallowedHost):
            request_origin(request)


class TestOrigin:
    """An origin builds absolute URLs and the site object a Django sitemap reads."""

    def test_url_joins_the_path(self) -> None:
        assert Origin("https", "acme.example").url("/a/") == "https://acme.example/a/"

    def test_the_site_carries_the_domain_as_a_request_site(self) -> None:
        site = Origin("https", "acme.example").site
        assert isinstance(site, RequestSite)
        assert (site.domain, site.name) == ("acme.example", "acme.example")


class TestSitesFramework:
    """An installed sites framework names the domain before the request host does."""

    @override_settings(SITE_ID=1)
    def test_the_site_id_row_names_the_domain(self, site_model) -> None:
        site_model.objects.create(id=1, domain="sites.example", name="Sites")
        request = RequestFactory().get("/", HTTP_HOST="testserver")
        assert request_origin(request) == Origin("http", "sites.example")

    def test_a_table_without_the_host_falls_back_to_the_request(
        self, site_model
    ) -> None:
        site_model.objects.create(id=1, domain="sites.example", name="Sites")
        request = RequestFactory().get("/", HTTP_HOST="testserver")
        assert request_origin(request) == Origin("http", "testserver")

    @override_settings(SITE_ID=1)
    def test_the_sitemap_lists_on_the_site_domain(self, site_model, tmp_path) -> None:
        site_model.objects.create(id=1, domain="sites.example", name="Sites")
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="")
        with routed(root):
            response = Client().get("/sitemap.xml")
        assert response.status_code == 200
        assert [url.loc for url in parse_sitemap(response)] == [
            "http://sites.example/about/"
        ]

    @override_settings(SITE_ID=1)
    def test_the_canonical_names_the_origin_the_sitemap_lists(
        self, site_model, tmp_path
    ) -> None:
        site_model.objects.create(id=1, domain="sites.example", name="Sites")
        page, loc = _canonical_and_loc(write_metadata_tree(tmp_path / "pages", ABOUT))
        assert loc == "http://sites.example/about/"
        assert_metadata(page, canonical=loc)

    def test_without_a_site_row_both_follow_the_request(
        self, site_model, tmp_path
    ) -> None:
        site_model.objects.create(id=1, domain="sites.example", name="Sites")
        root = write_metadata_tree(tmp_path / "pages", ABOUT)
        page, loc = _canonical_and_loc(root, secure=True)
        assert loc == "https://testserver/about/"
        assert_metadata(page, canonical=loc)
