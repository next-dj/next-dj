import logging

import pytest
from django.core.exceptions import BadRequest, DisallowedHost, ImproperlyConfigured
from django.http import Http404, HttpRequest
from django.test import RequestFactory, override_settings

from next.conf import next_framework_settings
from next.site import SiteOriginError, site_indexable, site_origin, site_url
from next.site.config import (
    SITE_ORIGIN_ATTR,
    SiteConfig,
    forget_site_config,
    indexable_without_request,
    is_url_literal,
    site_closed_to_crawlers,
    site_config,
    site_url_failed,
    url_origin,
)
from tests.support import BASE, site_settings


def tenant_url(request: HttpRequest | None) -> str | None:
    return None if request is None else f"https://{request.get_host()}"


def empty_url(request: HttpRequest | None) -> str:
    return ""


def schemeless_url(request: HttpRequest | None) -> str:
    return "://"


def path_url(request: HttpRequest | None) -> str:
    return "https://tenant.example/app"


def text_less_url(request: HttpRequest | None) -> object:
    return 42


def live_only(request: HttpRequest | None) -> bool:
    return request is None or request.get_host() == "acme.example"


def raising_rule(request: HttpRequest | None) -> bool:
    CALLED.append(request)
    msg = "tenant table down"
    raise RuntimeError(msg)


def counted_indexable(request: HttpRequest | None) -> bool:
    CALLED.append(request)
    return True


def counted_url(request: HttpRequest | None) -> str:
    CALLED.append(request)
    return "https://tenant.example"


CALLED: list[object] = []
NOT_CALLABLE = "not callable"


@pytest.fixture(autouse=True)
def _fresh_calls():
    CALLED.clear()
    yield
    CALLED.clear()


def _records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "next.site.config"]


def _get(host: str, *, secure: bool = False) -> HttpRequest:
    return RequestFactory().get("/", HTTP_HOST=host, secure=secure)


class TestSiteConfig:
    """The `SITE` scope is read leniently once per reload."""

    def test_the_default_is_auto_without_url_or_name(self) -> None:
        assert site_config() == SiteConfig()

    def test_the_config_is_memoised_until_a_reload(self) -> None:
        first = site_config()
        assert site_config() is first
        next_framework_settings.reload()
        assert site_config() is not first

    def test_forgetting_clears_the_memo(self) -> None:
        site_config()
        forget_site_config()
        assert site_config.cache_info().currsize == 0

    def test_a_key_left_out_reads_the_default(self) -> None:
        with site_settings(NAME="Acme"):
            assert site_config() == SiteConfig(name="Acme")

    @pytest.mark.parametrize(
        ("name", "expected"), [("Acme", "Acme"), (1, None)], ids=["text", "not_text"]
    )
    def test_the_name_must_be_text(self, name: object, expected: object) -> None:
        with site_settings(NAME=name):
            assert site_config().name == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(BASE, True), ("app.site.url", False), ("", False)],
        ids=["address", "dotted_path", "empty"],
    )
    def test_an_address_is_told_from_a_dotted_path(
        self, value: str, *, expected: bool
    ) -> None:
        assert is_url_literal(value) is expected


class TestSiteUrl:
    """`site_url` answers the literal, a callable's answer, or nothing."""

    def test_no_url_answers_none(self) -> None:
        assert site_url() is None

    def test_a_literal_is_answered_as_its_origin(self) -> None:
        with site_settings(URL=f"{BASE}/"):
            assert site_url() == BASE

    @pytest.mark.parametrize(
        "rule", [tenant_url, f"{__name__}.tenant_url"], ids=["callable", "dotted"]
    )
    @override_settings(ALLOWED_HOSTS=["*"])
    def test_a_callable_answers_per_request(self, rule: object) -> None:
        with site_settings(URL=rule):
            assert site_url(_get("tenant.example")) == "https://tenant.example"
            assert site_url() is None

    @pytest.mark.parametrize(
        "rule",
        [
            empty_url,
            schemeless_url,
            path_url,
            text_less_url,
            f"{__name__}.NOT_CALLABLE",
            "nowhere.at_all",
            42,
            "",
            "https://acme.example/app/",
            "ftp://acme.example",
        ],
        ids=[
            "empty_answer",
            "schemeless_answer",
            "path_answer",
            "not_text_answer",
            "not_callable",
            "missing",
            "not_text",
            "empty",
            "literal_with_path",
            "literal_ftp",
        ],
    )
    def test_an_unusable_rule_answers_none(self, rule: object) -> None:
        with site_settings(URL=rule):
            assert site_url(_get("testserver")) is None


class TestUrlOrigin:
    """Only an http or https origin with no path, query or fragment splits."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (BASE, ("https", "acme.example")),
            (f"{BASE}/", ("https", "acme.example")),
            ("http://localhost:8000", ("http", "localhost:8000")),
            (f"{BASE}/app", None),
            (f"{BASE}/?x=1", None),
            (f"{BASE}/#top", None),
            ("https://", None),
            ("http://[::1", None),
            ("://", None),
            ("https://user:secret@acme.example", None),
            ("https://acme.example:99999", None),
            ("https://bad host.example", None),
        ],
        ids=[
            "origin",
            "slash",
            "port",
            "path",
            "query",
            "fragment",
            "no_host",
            "malformed",
            "schemeless",
            "credentials",
            "port_out_of_range",
            "invalid_host",
        ],
    )
    def test_the_origin_splits(
        self, value: str, expected: tuple[str, str] | None
    ) -> None:
        assert url_origin(value) == expected


class TestSiteOrigin:
    """The site URL wins, then the current site of the request, else it raises."""

    def test_the_site_url_wins_with_or_without_a_request(self) -> None:
        with site_settings(URL=f"{BASE}/"):
            assert site_origin(None) == ("https", "acme.example")
            assert site_origin(_get("testserver")) == ("https", "acme.example")

    def test_without_a_site_url_the_request_host_answers(self) -> None:
        request = _get("testserver", secure=True)
        assert site_origin(request) == ("https", "testserver")

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_an_unusable_callable_answer_falls_back_to_the_request(self) -> None:
        with site_settings(URL=path_url):
            assert site_origin(_get("testserver")) == ("http", "testserver")

    def test_a_request_free_caller_without_a_site_url_raises(self) -> None:
        with pytest.raises(SiteOriginError):
            site_origin(None)

    def test_a_request_holds_its_origin_for_every_later_url(self) -> None:
        request = _get("testserver")
        first = site_origin(request)
        assert getattr(request, SITE_ORIGIN_ATTR) == first
        with site_settings(URL=BASE):
            assert site_origin(request) == first
            assert site_origin(_get("testserver")) == ("https", "acme.example")

    @override_settings(SITE_ID=1)
    def test_the_sites_row_names_the_host(self, site_model) -> None:
        site_model.objects.create(id=1, domain="sites.example", name="Sites")
        assert site_origin(_get("testserver")) == ("http", "sites.example")

    def test_a_table_without_the_host_falls_back_to_the_request(
        self, site_model
    ) -> None:
        site_model.objects.create(id=1, domain="sites.example", name="Sites")
        assert site_origin(_get("testserver")) == ("http", "testserver")


class TestSiteIndexable:
    """The one answer every head tag, sitemap and robots.txt reads."""

    @pytest.mark.parametrize(
        ("debug", "expected"), [(False, True), (True, False)], ids=["prod", "debug"]
    )
    def test_auto_follows_debug(self, *, debug: bool, expected: bool) -> None:
        with override_settings(DEBUG=debug):
            assert site_indexable() is expected
            with site_settings(INDEXABLE="auto"):
                assert site_indexable(_get("testserver")) is expected

    @pytest.mark.parametrize("value", [True, False])
    def test_a_bool_is_taken_as_written(self, *, value: bool) -> None:
        with site_settings(INDEXABLE=value), override_settings(DEBUG=not value):
            assert site_indexable() is value

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_a_callable_decides_with_or_without_a_request(self) -> None:
        with site_settings(INDEXABLE=live_only):
            assert site_indexable() is True
            assert site_indexable(_get("acme.example")) is True
            assert site_indexable(_get("preview.example")) is False

    @pytest.mark.parametrize(
        "rule",
        [42, f"{__name__}.live_only", ["acme.example"]],
        ids=["number", "dotted_path", "host_list"],
    )
    def test_an_unusable_rule_falls_back_to_auto(self, rule: object) -> None:
        with site_settings(INDEXABLE=rule), override_settings(DEBUG=True):
            assert site_indexable() is False

    def test_a_callable_is_called_once_per_request(self) -> None:
        request = _get("testserver")
        with site_settings(INDEXABLE=counted_indexable):
            assert site_indexable(request) is True
            assert site_indexable(request) is True
            assert site_indexable(None) is True
        assert [request, None] == CALLED

    def test_the_rule_reads_debug_on_every_call(self) -> None:
        site_config()
        with override_settings(DEBUG=True):
            assert site_indexable() is False
        assert site_indexable() is True


class TestFailingSiteUrl:
    """A callable `URL` that raises or answers no origin is reported, never a 500."""

    @pytest.mark.parametrize("rule", [raising_rule, path_url, text_less_url])
    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_production_logs_once_and_declares_no_origin(self, rule, caplog) -> None:
        with site_settings(URL=rule), caplog.at_level(logging.WARNING):
            first, second = _get("testserver"), _get("testserver")
            assert site_url(first) is None
            assert site_url_failed(first) is True
            assert site_origin(second) == ("http", "testserver")
            assert site_url_failed(second) is True
        [record] = _records(caplog)
        assert "NEXT_FRAMEWORK['SITE']['URL']" in record.getMessage()
        assert f'"{rule.__name__}"' in record.getMessage()

    @pytest.mark.parametrize(
        "exc", [DisallowedHost("bad host"), BadRequest("bad"), Http404("gone")]
    )
    def test_an_intended_exception_reaches_django(self, exc) -> None:
        # A bad Host answers Django's 400 and its security log, not a 503.
        def rule(request):
            raise exc

        with site_settings(URL=rule), pytest.raises(type(exc)):
            site_url(_get("testserver"))

    def test_a_request_calls_the_rule_once(self) -> None:
        request = _get("testserver")
        with site_settings(URL=counted_url):
            assert site_url(request) == "https://tenant.example"
            assert site_origin(request) == ("https", "tenant.example")
            assert site_url_failed(request) is False
        assert [request] == CALLED

    def test_a_request_free_caller_raises_on_a_failing_rule(self) -> None:
        with site_settings(URL=raising_rule), pytest.raises(SiteOriginError):
            site_origin(None)

    def test_declining_an_origin_is_no_failure(self) -> None:
        with site_settings(URL=tenant_url):
            assert site_url_failed(None) is False

    @pytest.mark.parametrize(
        ("rule", "named"),
        [
            (raising_rule, "raised RuntimeError"),
            (path_url, "'https://tenant.example/app'"),
        ],
        ids=["raising", "path"],
    )
    @override_settings(DEBUG=True)
    def test_debug_raises_naming_the_setting(self, rule, named) -> None:
        with site_settings(URL=rule), pytest.raises(ImproperlyConfigured) as caught:
            site_url(_get("testserver"))
        assert "NEXT_FRAMEWORK['SITE']['URL']" in str(caught.value)
        assert named in str(caught.value)


class TestFailingIndexable:
    """A callable `INDEXABLE` that raises closes the site, loud only under `DEBUG`."""

    def test_production_fails_closed_and_logs_once(self, caplog) -> None:
        with site_settings(INDEXABLE=raising_rule), caplog.at_level(logging.ERROR):
            assert site_indexable(_get("testserver")) is False
            assert site_indexable(_get("testserver")) is False
        [record] = _records(caplog)
        assert "closed to search" in record.getMessage()
        assert '"raising_rule"' in record.getMessage()

    @override_settings(DEBUG=True)
    def test_debug_raises_with_the_rule_named(self) -> None:
        with (
            site_settings(INDEXABLE=raising_rule),
            pytest.raises(RuntimeError, match="tenant table down") as caught,
        ):
            site_indexable(_get("testserver"))
        assert "INDEXABLE" in caught.value.__notes__[0]


class TestStaticAnswers:
    """The answers the SEO routes and the checks read without calling a rule twice."""

    @pytest.mark.parametrize(
        ("indexable", "debug", "expected"),
        [(None, False, True), (None, True, False), (False, False, False)],
        ids=["auto", "auto_debug", "closed"],
    )
    def test_without_a_request_no_rule_is_called(
        self, indexable, *, debug: bool, expected: bool
    ) -> None:
        site = {} if indexable is None else {"INDEXABLE": indexable}
        with site_settings(**site), override_settings(DEBUG=debug):
            assert indexable_without_request() is expected
        with site_settings(INDEXABLE=raising_rule):
            assert indexable_without_request() is True
        assert CALLED == []

    @pytest.mark.parametrize(
        ("site", "debug", "closed"),
        [({}, True, False), ({"INDEXABLE": False}, True, True), ({}, False, False)],
        ids=["debug_preview", "explicit", "open"],
    )
    def test_closed_to_crawlers(self, site, *, debug: bool, closed: bool) -> None:
        with site_settings(**site), override_settings(DEBUG=debug):
            assert site_closed_to_crawlers(_get("testserver")) is closed
