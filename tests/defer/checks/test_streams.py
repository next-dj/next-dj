from pathlib import Path
from unittest.mock import patch

import django
import pytest
from django.core.checks.registry import registry
from django.test import override_settings

from next.defer.checks import (
    check_buffering_gzip,
    check_bundles_have_a_runtime,
    check_stream_nonce,
    streams as streams_module,
)
from next.testing import override_next_settings
from tests.support import write_page
from tests.support.access import PAGE_HEAD
from tests.support.checks import run_check


DISABLED = {"policy": "disabled"}
LOADING_ZONE = '{% zone "rows" reload="loading" %}<p>r</p>{% endzone %}'
WINDOWED_MORE = (
    '{% zone "rows" %}<p>r</p>{% endzone %}'
    '{% more zone="rows" next=n prune=10 %}More{% endmore %}'
)


@pytest.fixture()
def no_tokens():
    with patch.object(streams_module, "mints_tokens", return_value=False):
        yield


@pytest.mark.usefixtures("no_tokens")
class TestBundlesHaveARuntime:
    """next.W181 flags a `disabled` policy on a project whose pages need a bundle."""

    def test_a_loading_zone_is_flagged(self, root: Path) -> None:
        write_page(root, "list", PAGE_HEAD, body=LOADING_ZONE)

        [message] = run_check(
            check_bundles_have_a_runtime, root, NEXT_JS_OPTIONS=DISABLED
        )

        assert message.id == "next.W181"
        assert 'the zone "rows" of ' in message.msg
        assert "reloads to its loading UI" in message.msg
        assert "'auto'" in message.hint

    def test_a_windowed_more_is_flagged(self, root: Path) -> None:
        write_page(root, "feed", PAGE_HEAD, body=WINDOWED_MORE)

        [message] = run_check(
            check_bundles_have_a_runtime, root, NEXT_JS_OPTIONS=DISABLED
        )

        assert "sets prune= or url=" in message.msg

    def test_a_component_minting_tokens_is_flagged(self, root: Path) -> None:
        write_page(root, "plain", PAGE_HEAD, body="<p>plain</p>")

        with patch.object(streams_module, "mints_tokens", return_value=True):
            [message] = run_check(
                check_bundles_have_a_runtime, root, NEXT_JS_OPTIONS=DISABLED
            )

        assert "a component loads on a later request" in message.msg

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param('{% zone "rows" %}r{% endzone %}', id="plain-zone"),
            pytest.param(
                WINDOWED_MORE.replace(" prune=10", ""), id="more-without-window"
            ),
        ],
    )
    def test_a_project_needing_no_bundle_is_silent(self, root: Path, body: str) -> None:
        write_page(root, "plain", PAGE_HEAD, body=body)

        assert (
            run_check(check_bundles_have_a_runtime, root, NEXT_JS_OPTIONS=DISABLED)
            == []
        )

    @pytest.mark.parametrize(
        "options",
        [
            pytest.param({}, id="auto"),
            pytest.param({"policy": "manual"}, id="manual"),
            pytest.param({"policy": "sometimes"}, id="unknown"),
        ],
    )
    def test_a_policy_injecting_the_runtime_is_silent(
        self, root: Path, options: dict[str, str]
    ) -> None:
        write_page(root, "list", PAGE_HEAD, body=LOADING_ZONE)

        assert (
            run_check(check_bundles_have_a_runtime, root, NEXT_JS_OPTIONS=options) == []
        )

    def test_options_of_the_wrong_shape_are_left_to_the_type_check(self) -> None:
        with patch.object(streams_module, "next_framework_settings") as framework:
            framework.NEXT_JS_OPTIONS = []
            assert check_bundles_have_a_runtime() == []


_GZIP = "django.middleware.gzip.GZipMiddleware"
_NEXT = "next.middleware.CompressionMiddleware"
_DJANGO_CSP = "django.middleware.csp.ContentSecurityPolicyMiddleware"
_NO_STREAM = {"DELIVERY": {"OPTIONS": {"STREAM": False}}}
_DJANGO_60 = (6, 0, 0, "final", 0)


class TestBufferingGzip:
    """next.W153 flags Django's GZipMiddleware holding page streams back below 6.1."""

    def test_an_old_django_with_gzip_and_no_compression_is_flagged(self) -> None:
        with (
            patch.object(django, "VERSION", _DJANGO_60),
            override_settings(MIDDLEWARE=[_GZIP]),
            override_next_settings(COMPRESSION={"ENABLED": False}),
        ):
            [message] = check_buffering_gzip()
        assert message.id == "next.W153"
        assert "assembled inline rather than streamed" in message.msg

    @pytest.mark.parametrize(
        ("version", "middleware", "framework"),
        [
            pytest.param((6, 1, 0, "final", 0), [_GZIP], {}, id="flushing-django"),
            pytest.param(_DJANGO_60, [], {}, id="no-gzip"),
            pytest.param(_DJANGO_60, [_GZIP], {"DEFER": _NO_STREAM}, id="no-stream"),
        ],
    )
    def test_anything_else_is_quiet(
        self, version: tuple[object, ...], middleware: list[str], framework: dict
    ) -> None:
        with (
            patch.object(django, "VERSION", version),
            override_settings(MIDDLEWARE=middleware),
            override_next_settings(COMPRESSION={"ENABLED": False}, **framework),
        ):
            assert check_buffering_gzip() == []

    def test_streams_left_to_an_absent_next_middleware_are_flagged(self) -> None:
        with (
            patch.object(django, "VERSION", _DJANGO_60),
            override_settings(MIDDLEWARE=[_GZIP]),
            override_next_settings(COMPRESSION={"STREAMS": "middleware"}),
        ):
            [message] = check_buffering_gzip()
        assert message.id == "next.W153"

    @pytest.mark.parametrize(
        ("middleware", "streams"),
        [
            pytest.param([_GZIP], "self", id="self"),
            pytest.param([_NEXT, _GZIP], "middleware", id="next-middleware"),
        ],
    )
    def test_next_compression_keeps_it_quiet(
        self, middleware: list[str], streams: str
    ) -> None:
        with (
            patch.object(django, "VERSION", _DJANGO_60),
            override_settings(MIDDLEWARE=middleware),
            override_next_settings(COMPRESSION={"STREAMS": streams}),
        ):
            assert check_buffering_gzip() == []


class TestStreamNonce:
    """next.W160 flags a CSP that refuses the inline scripts of a page stream."""

    @pytest.mark.parametrize(
        ("policy", "nonce", "reason"),
        [
            pytest.param({"script-src": ["'self'"]}, True, "hold no CSP.NONCE", id="b"),
            pytest.param(
                {"script-src": None, "default-src": "'self'"},
                True,
                "hold no CSP.NONCE",
                id="default-src",
            ),
            pytest.param(
                {"script-src": ["<CSP_NONCE_SENTINEL>"]},
                False,
                "CSP_NONCE'] is off",
                id="nonce-off",
            ),
            pytest.param(
                {"script-src": ["'unsafe-inline'", "'strict-dynamic'"]},
                False,
                "CSP_NONCE'] is off",
                id="inline-ignored",
            ),
        ],
    )
    def test_a_refusing_django_policy_is_flagged(
        self, policy: dict, *, nonce: bool, reason: str
    ) -> None:
        with (
            override_settings(MIDDLEWARE=[_DJANGO_CSP], SECURE_CSP=policy),
            override_next_settings(CSP_NONCE=nonce),
        ):
            [message] = check_stream_nonce()
        assert message.id == "next.W160"
        assert reason in message.msg

    @pytest.mark.parametrize(
        "policy",
        [
            pytest.param({}, id="no-policy"),
            pytest.param({"style-src": ["'self'"]}, id="scripts-open"),
            pytest.param("odd", id="odd-policy"),
            pytest.param({"script-src": ["<CSP_NONCE_SENTINEL>"]}, id="nonce"),
            pytest.param({"script-src": "'self' 'unsafe-inline'"}, id="inline"),
        ],
    )
    def test_a_policy_letting_the_scripts_run_is_quiet(self, policy: object) -> None:
        with override_settings(MIDDLEWARE=[_DJANGO_CSP], SECURE_CSP=policy):
            assert check_stream_nonce() == []

    def test_django_csp_is_read_through_the_nonce_setting_only(self) -> None:
        with override_settings(MIDDLEWARE=["csp.middleware.CSPMiddleware"]):
            assert check_stream_nonce() == []
            with override_next_settings(CSP_NONCE=False):
                [message] = check_stream_nonce()
        assert "CSP_NONCE'] is off" in message.msg

    def test_no_csp_or_no_stream_is_quiet(self) -> None:
        with override_next_settings(CSP_NONCE=False):
            assert check_stream_nonce() == []
        with (
            override_settings(MIDDLEWARE=[_DJANGO_CSP], SECURE_CSP={"script-src": []}),
            override_next_settings(DEFER=_NO_STREAM),
        ):
            assert check_stream_nonce() == []


def test_the_stream_checks_are_registered() -> None:
    registered = set(registry.get_checks())
    assert {
        check_buffering_gzip,
        check_bundles_have_a_runtime,
        check_stream_nonce,
    } <= registered
