import pytest
from django.http import HttpRequest
from django.test import RequestFactory, override_settings

from next.conf import next_framework_settings
from next.site import (
    SiteConfig,
    SiteOriginError,
    site_config,
    site_indexable,
    site_origin,
    site_url,
)
from next.site.config import (
    SITE_ORIGIN_ATTR,
    forget_site_config,
    is_url_literal,
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


NOT_CALLABLE = "not callable"


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

    def test_the_rule_reads_debug_on_every_call(self) -> None:
        site_config()
        with override_settings(DEBUG=True):
            assert site_indexable() is False
        assert site_indexable() is True
