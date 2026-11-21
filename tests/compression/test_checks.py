import pytest
from django.core.checks import run_checks
from django.test import override_settings

from next.checks import NEXT
from next.compression.checks import (
    GZIP_MIDDLEWARE,
    MIDDLEWARE,
    check_compression_codecs,
    check_compression_cookies,
    check_compression_middleware,
    check_compression_paths,
    check_compression_settings,
)
from next.middleware import CompressionMiddleware
from next.testing import override_next_settings
from tests.support.compressors import fake_brotli, fake_zstandard, install


SECURITY = "django.middleware.security.SecurityMiddleware"
COMMON = "django.middleware.common.CommonMiddleware"
CONDITIONAL = "django.middleware.http.ConditionalGetMiddleware"
FETCH = "django.middleware.cache.FetchFromCacheMiddleware"
CACHE = "django.middleware.cache.CacheMiddleware"
UPDATE = "django.middleware.cache.UpdateCacheMiddleware"


class CustomCompression:
    pass


class Subclassed(CompressionMiddleware):
    pass


def _compression(**scope: object):
    return override_next_settings(COMPRESSION=scope)


def _ids(messages) -> list[str]:
    return [message.id for message in messages]


class TestSettingsShape:
    """`check_compression_settings` reports unknown keys and misfit values."""

    def test_silent_without_the_scope(self) -> None:
        with override_settings(NEXT_FRAMEWORK={}):
            assert check_compression_settings() == []

    def test_a_non_dict_scope_is_left_to_the_type_check(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"COMPRESSION": "on"}):
            assert check_compression_settings() == []

    def test_silent_on_a_valid_scope(self) -> None:
        with _compression(ENABLED=False, LEVELS={"gzip": 6}, CONTENT_TYPES=["text/*"]):
            assert check_compression_settings() == []

    def test_an_unknown_key_yields_e035(self) -> None:
        with _compression(LEVEL=6):
            (message,) = check_compression_settings()
        assert message.id == "next.E035"
        assert "NEXT_FRAMEWORK['COMPRESSION'] has unknown keys 'LEVEL'" in message.msg

    def test_a_misfit_yields_e076(self) -> None:
        with _compression(MIN_SIZE="1k", STREAMS="proxy"):
            messages = check_compression_settings()
        assert _ids(messages) == ["next.E076", "next.E076"]
        assert "NEXT_FRAMEWORK['COMPRESSION']['MIN_SIZE'] must be" in messages[0].msg
        assert "'self' or 'middleware'" in messages[1].msg


class TestPaths:
    """`check_compression_paths` reports unusable backend and exclude paths."""

    def test_silent_by_default(self) -> None:
        assert check_compression_paths() == []

    @pytest.mark.parametrize(
        ("dotted", "problem"),
        [
            ("next.compression.Missing", "does not import"),
            (f"{__name__}.CustomCompression", "no subclass of CompressionBackend"),
            ("next.compression.CompressionBackend", "abstract class"),
        ],
    )
    def test_a_backend_path(self, dotted: str, problem: str) -> None:
        with _compression(BACKENDS=[dotted]):
            (message,) = check_compression_paths()
        assert message.id == "next.E219"
        assert "['BACKENDS']" in message.msg
        assert problem in message.msg

    @pytest.mark.parametrize(
        ("dotted", "problem"),
        [
            (f"{__name__}.missing", "does not import"),
            (f"{__name__}.CACHE", "no callable"),
        ],
    )
    def test_an_exclude_path(self, dotted: str, problem: str) -> None:
        with _compression(EXCLUDE=dotted):
            (message,) = check_compression_paths()
        assert message.id == "next.E219"
        assert "['EXCLUDE']" in message.msg
        assert problem in message.msg

    def test_a_callable_exclude_is_silent(self) -> None:
        with _compression(EXCLUDE=f"{__name__}._ids"):
            assert check_compression_paths() == []


class TestCodecs:
    """`check_compression_codecs` reports codings, ranges and missing libraries."""

    def test_silent_with_every_library(self, monkeypatch) -> None:
        install(monkeypatch, zstandard=fake_zstandard(), brotli=fake_brotli())
        assert check_compression_codecs() == []

    def test_a_missing_core_codec_is_refused(self, monkeypatch) -> None:
        install(monkeypatch)
        (message,) = check_compression_codecs()
        assert message.id == "next.E228"
        assert "offers 'zstd'" in message.msg

    def test_a_missing_optional_codec_needs_a_written_order(self, monkeypatch) -> None:
        install(monkeypatch, zstandard=fake_zstandard())
        assert check_compression_codecs() == []
        with _compression(ORDER=["br", "gzip"]):
            (message,) = check_compression_codecs()
        assert "offers 'br'" in message.msg
        assert "'brotli' extra" in message.hint

    def test_disabled_skips_the_libraries(self, monkeypatch) -> None:
        install(monkeypatch)
        with _compression(ENABLED=False, SSE_TOPICS="reset", ORDER=["br"]):
            assert check_compression_codecs() == []

    def test_unknown_codings(self, monkeypatch) -> None:
        install(monkeypatch, zstandard=fake_zstandard(), brotli=fake_brotli())
        with _compression(
            ORDER=["gzip", "deflate", "deflate"], LEVELS={"lz4": 1}, WINDOWS={"xz": 20}
        ):
            messages = check_compression_codecs()
        assert _ids(messages) == ["next.E228"] * 3
        assert "['ORDER'] names the coding 'deflate'" in messages[0].msg
        assert "['LEVELS'] names the coding 'lz4'" in messages[1].msg
        assert "['WINDOWS'] names the coding 'xz'" in messages[2].msg
        assert "br, gzip, zstd" in messages[0].hint

    def test_no_backend_at_all(self) -> None:
        with _compression(BACKENDS=["next.compression.Missing"], ORDER=["gzip"]):
            (message,) = check_compression_codecs()
        assert "(none)" in message.hint

    def test_levels_and_windows_out_of_range(self, monkeypatch) -> None:
        install(monkeypatch, zstandard=fake_zstandard(), brotli=fake_brotli())
        with _compression(
            LEVELS={"gzip": 10, "br": 11, "zstd": 0},
            WINDOWS={"gzip": 15, "br": 25, "zstd": 20},
        ):
            messages = check_compression_codecs()
        texts = [message.msg for message in messages]
        assert _ids(messages) == ["next.E228"] * 4
        assert "['LEVELS']['gzip'] is 10, outside 1 to 9" in texts[0]
        assert "['LEVELS']['zstd'] is 0, outside 1 to 22" in texts[1]
        assert "['WINDOWS']['gzip'] is 15, while the codec takes no window" in texts[2]
        assert "['WINDOWS']['br'] is 25, while the codec takes 10 to 24" in texts[3]

    def test_a_zstd_window_past_what_browsers_decode_is_refused(
        self, monkeypatch
    ) -> None:
        install(monkeypatch, zstandard=fake_zstandard())
        with _compression(WINDOWS={"zstd": 24}):
            (message,) = check_compression_codecs()
        assert "['WINDOWS']['zstd'] is 24, while the codec takes 10 to 23" in (
            message.msg
        )

    def test_reset_topics_need_a_resetting_codec(self, monkeypatch) -> None:
        install(monkeypatch, zstandard=fake_zstandard(), brotli=fake_brotli())
        with _compression(SSE_TOPICS="reset", ORDER=["br"]):
            (message,) = check_compression_codecs()
        assert "'SSE_TOPICS'] is 'reset'" in message.msg
        with _compression(SSE_TOPICS="reset"):
            assert check_compression_codecs() == []


class TestMiddleware:
    """`check_compression_middleware` reports the pair, the absence and the order."""

    @pytest.mark.parametrize(
        "middleware",
        [
            [SECURITY, MIDDLEWARE, COMMON, CONDITIONAL],
            [UPDATE, SECURITY, MIDDLEWARE, COMMON, FETCH],
            [MIDDLEWARE],
        ],
    )
    def test_silent_in_order(self, middleware) -> None:
        with override_settings(MIDDLEWARE=middleware):
            assert check_compression_middleware() == []

    def test_both_compressors_yield_e227(self) -> None:
        with override_settings(MIDDLEWARE=[MIDDLEWARE, GZIP_MIDDLEWARE]):
            (message,) = check_compression_middleware()
        assert message.id == "next.E227"
        assert GZIP_MIDDLEWARE in message.hint

    def test_a_missing_middleware_yields_w164(self) -> None:
        with override_settings(MIDDLEWARE=[SECURITY, GZIP_MIDDLEWARE]):
            (message,) = check_compression_middleware()
        assert message.id == "next.W164"

    def test_a_missing_middleware_while_disabled_is_silent(self) -> None:
        with override_settings(MIDDLEWARE=[]), _compression(ENABLED=False):
            assert check_compression_middleware() == []

    @pytest.mark.parametrize(
        ("middleware", "named"),
        [
            ([COMMON, MIDDLEWARE], [COMMON]),
            ([CONDITIONAL, COMMON, MIDDLEWARE], [COMMON, CONDITIONAL]),
            ([CACHE, MIDDLEWARE], [FETCH]),
            ([FETCH, MIDDLEWARE, COMMON], [FETCH]),
        ],
    )
    def test_listed_below_a_body_reader_yields_w165(self, middleware, named) -> None:
        with override_settings(MIDDLEWARE=middleware):
            (message,) = check_compression_middleware()
        assert message.id == "next.W165"
        assert f"below {', '.join(named)}," in message.msg

    def test_a_subclass_counts(self) -> None:
        with override_settings(MIDDLEWARE=[f"{__name__}.Subclassed", GZIP_MIDDLEWARE]):
            assert _ids(check_compression_middleware()) == ["next.E227"]

    def test_entries_that_do_not_import_are_skipped(self) -> None:
        with override_settings(MIDDLEWARE=["missing.Middleware", 42, MIDDLEWARE]):
            assert check_compression_middleware() == []


class TestCookies:
    """`check_compression_cookies` warns about cross-site cookies (`next.W166`)."""

    @pytest.mark.parametrize(
        ("session", "csrf", "named"),
        [
            ("None", "Lax", "SESSION_COOKIE_SAMESITE"),
            ("Lax", "none", "CSRF_COOKIE_SAMESITE"),
            ("None", "None", "SESSION_COOKIE_SAMESITE and CSRF_COOKIE_SAMESITE"),
        ],
    )
    def test_a_cross_site_cookie(self, session, csrf, named) -> None:
        with override_settings(
            SESSION_COOKIE_SAMESITE=session, CSRF_COOKIE_SAMESITE=csrf
        ):
            (message,) = check_compression_cookies()
        assert message.id == "next.W166"
        assert message.msg.startswith(f"{named} is 'None'")

    @pytest.mark.parametrize("value", ["Lax", "Strict", False, None])
    def test_silent_otherwise(self, value) -> None:
        with override_settings(
            SESSION_COOKIE_SAMESITE=value, CSRF_COOKIE_SAMESITE=value
        ):
            assert check_compression_cookies() == []

    def test_silent_while_disabled(self) -> None:
        with (
            override_settings(SESSION_COOKIE_SAMESITE="None"),
            _compression(ENABLED=False),
        ):
            assert check_compression_cookies() == []


def test_the_checks_run_under_the_next_tag(monkeypatch) -> None:
    install(monkeypatch)
    with override_settings(MIDDLEWARE=[COMMON, MIDDLEWARE, GZIP_MIDDLEWARE]):
        found = set(_ids(run_checks(tags=[NEXT])))
    assert {"next.E227", "next.E228", "next.W165"} <= found
