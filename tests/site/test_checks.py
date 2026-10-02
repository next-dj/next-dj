from collections.abc import Iterator

import pytest
from django.conf import settings
from django.http import HttpRequest
from django.test import override_settings
from django.utils.translation import gettext_lazy

from next.site.checks import check_site_settings, check_site_url_for_deploy
from tests.support import check_ids, site_settings


SITES_APPS = [*settings.INSTALLED_APPS, "django.contrib.sites"]
ORIGIN = "https://acme.example"


def preview_hosts(request: HttpRequest | None) -> bool:
    return request is None


def no_parameter() -> bool:
    return True


def two_parameters(request: HttpRequest | None, host: str) -> bool:
    return True


def keyword_only(*, request: HttpRequest | None) -> bool:
    return True


def raising_rule(request: HttpRequest | None) -> bool:
    raise AssertionError


@pytest.fixture()
def production() -> Iterator[None]:
    with override_settings(DEBUG=False):
        yield


class TestSiteSettings:
    """`check_site_settings` reads the raw `SITE` scope."""

    @pytest.mark.parametrize(
        "site",
        [
            {},
            {"URL": "https://acme.example"},
            {"URL": "http://localhost:8000/"},
            {"URL": preview_hosts},
            {"URL": f"{__name__}.preview_hosts"},
            {"NAME": "Acme"},
            {"NAME": gettext_lazy("Acme")},
            {"INDEXABLE": "auto"},
            {"INDEXABLE": False},
            {"INDEXABLE": preview_hosts},
        ],
        ids=[
            "empty",
            "origin",
            "origin_with_port",
            "url_callable",
            "url_dotted",
            "name",
            "lazy_name",
            "auto",
            "closed",
            "indexable_callable",
        ],
    )
    def test_a_usable_scope_is_silent(self, site: dict[str, object]) -> None:
        with site_settings(**site):
            assert check_site_settings() == []

    def test_an_absent_or_mistyped_scope_is_left_to_the_conf_checks(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"SITE": "x"}):
            assert check_site_settings() == []
        with override_settings(NEXT_FRAMEWORK=[]):
            assert check_site_settings() == []

    @pytest.mark.parametrize(
        ("site", "key"),
        [
            ({"URL": "ftp://acme.example"}, "URL"),
            ({"URL": "https://"}, "URL"),
            ({"URL": "https://acme.example/app/"}, "URL"),
            ({"URL": "https://acme.example/?x=1"}, "URL"),
            ({"URL": "https://acme.example/#top"}, "URL"),
            ({"URL": "http://[::1"}, "URL"),
            ({"URL": "nowhere.at_all"}, "URL"),
            ({"URL": 42}, "URL"),
            ({"NAME": 1}, "NAME"),
            ({"INDEXABLE": "yes"}, "INDEXABLE"),
            ({"INDEXABLE": ["acme.example"]}, "INDEXABLE"),
            ({"INDEXABLE": f"{__name__}.preview_hosts"}, "INDEXABLE"),
            ({"INDEXABLE": 1}, "INDEXABLE"),
        ],
        ids=[
            "ftp",
            "no_host",
            "path",
            "query",
            "fragment",
            "malformed",
            "missing_path",
            "not_text",
            "name",
            "unknown_word",
            "host_list",
            "indexable_dotted",
            "number",
        ],
    )
    def test_an_unusable_value_is_e129(self, site: dict[str, object], key: str) -> None:
        with site_settings(**site):
            messages = check_site_settings()
        assert check_ids(messages) == ["next.E129"]
        assert f"NEXT_FRAMEWORK['SITE'][{key!r}]" in messages[0].msg

    @pytest.mark.parametrize("key", ["URL", "INDEXABLE"])
    @pytest.mark.parametrize(
        "rule",
        [no_parameter, two_parameters, keyword_only],
        ids=["none", "two", "keyword_only"],
    )
    def test_a_callable_that_cannot_take_the_request_is_e129(
        self, key: str, rule: object
    ) -> None:
        with site_settings(**{key: rule}):
            messages = check_site_settings()
        assert check_ids(messages) == ["next.E129"]
        assert f"NEXT_FRAMEWORK['SITE'][{key!r}] is \"{rule.__name__}\"" in (
            messages[0].msg
        )
        assert "one positional argument" in messages[0].msg

    def test_a_dotted_url_callable_is_read_for_its_signature(self) -> None:
        with site_settings(URL=f"{__name__}.no_parameter"):
            assert check_ids(check_site_settings()) == ["next.E129"]

    @pytest.mark.parametrize("rule", [len, raising_rule], ids=["builtin", "raising"])
    def test_a_callable_is_never_called(self, rule: object) -> None:
        with site_settings(URL=rule, INDEXABLE=rule):
            assert check_site_settings() == []

    def test_an_unknown_key_is_e035(self) -> None:
        with site_settings(HOST="acme.example"):
            messages = check_site_settings()
        assert check_ids(messages) == ["next.E035"]
        assert "'HOST'" in messages[0].msg


@pytest.mark.usefixtures("production")
class TestDeployWarnings:
    """The deploy check warns about a missing site URL."""

    def test_no_url_is_w119(self) -> None:
        assert check_ids(check_site_url_for_deploy()) == ["next.W119"]
        with override_settings(NEXT_FRAMEWORK={}):
            assert check_ids(check_site_url_for_deploy()) == ["next.W119"]

    def test_debug_does_not_silence_w119(self) -> None:
        """A deploy run states production intent, whatever DEBUG reads."""
        with override_settings(DEBUG=True):
            assert check_ids(check_site_url_for_deploy()) == ["next.W119"]

    def test_a_url_is_silent(self) -> None:
        with site_settings(URL="https://acme.example"):
            assert check_site_url_for_deploy() == []

    def test_a_pinned_sites_row_is_silent(self) -> None:
        with override_settings(INSTALLED_APPS=SITES_APPS, SITE_ID=1):
            assert check_site_url_for_deploy() == []

    def test_the_sites_app_without_a_site_id_still_warns(self) -> None:
        with override_settings(INSTALLED_APPS=SITES_APPS):
            assert check_ids(check_site_url_for_deploy()) == ["next.W119"]
        with override_settings(SITE_ID=1):
            assert check_ids(check_site_url_for_deploy()) == ["next.W119"]

    def test_any_host_allowed_escalates_to_w132(self) -> None:
        with override_settings(ALLOWED_HOSTS=["acme.example", "*"]):
            [warning] = check_site_url_for_deploy()
        assert warning.id == "next.W132"
        assert "ALLOWED_HOSTS holds '*'" in warning.msg
        with override_settings(ALLOWED_HOSTS=["*"]), site_settings(URL=ORIGIN):
            assert check_site_url_for_deploy() == []
