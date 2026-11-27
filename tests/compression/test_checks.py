from collections.abc import Iterator
from pathlib import Path

import pytest
from django.core.checks import run_checks
from django.test import override_settings

from next.checks import NEXT
from next.compression import GzipBackend
from next.compression.checks import (
    GZIP_MIDDLEWARE,
    MIDDLEWARE,
    check_compress_declarations,
    check_compression_codecs,
    check_compression_cookies,
    check_compression_middleware,
    check_compression_paths,
    check_compression_settings,
    check_stream_middleware,
)
from next.middleware import CompressionMiddleware
from next.pages.declarations import page_declarations
from next.pages.loaders import load_python_module_memo
from next.testing import override_next_settings
from tests.support.checks import written
from tests.support.compressors import fake_brotli, fake_zstandard, install


SECURITY = "django.middleware.security.SecurityMiddleware"
COMMON = "django.middleware.common.CommonMiddleware"
CONDITIONAL = "django.middleware.http.ConditionalGetMiddleware"
FETCH = "django.middleware.cache.FetchFromCacheMiddleware"
CACHE = "django.middleware.cache.CacheMiddleware"
UPDATE = "django.middleware.cache.UpdateCacheMiddleware"


GZIP = [{"BACKEND": "next.compression.GzipBackend"}]
ZSTD = "next.compression.ZstdBackend"
BROTLI = "next.compression.BrotliBackend"


class CustomCompression:
    pass


class Unshipped(GzipBackend):
    coding = "lz"

    def available(self) -> bool:
        return False


class Subclassed(CompressionMiddleware):
    pass


def _compression(**scope: object):
    return override_next_settings(COMPRESSION=scope)


def _entry(dotted: str, **options: int) -> dict[str, object]:
    return {"BACKEND": dotted, "OPTIONS": options}


def _ids(messages) -> list[str]:
    return [message.id for message in messages]


class TestSettingsShape:
    """`check_compression_settings` reports unknown keys and misfit values."""

    def test_silent_without_the_scope(self) -> None:
        with override_settings(NEXT_FRAMEWORK={}):
            assert check_compression_settings() == []

    def test_a_non_dict_scope_is_left_to_the_type_check(self) -> None:
        with written({"COMPRESSION": "on"}):
            assert check_compression_settings() == []

    def test_silent_on_a_valid_scope(self) -> None:
        with _compression(
            ENABLED=False,
            BACKENDS=[_entry("next.compression.GzipBackend", LEVEL=6)],
            CONTENT_TYPES=["text/*"],
        ):
            assert check_compression_settings() == []

    def test_an_unknown_key_yields_e035(self) -> None:
        with _compression(LEVEL=6):
            (message,) = check_compression_settings()
        assert message.id == "next.E035"
        assert "NEXT_FRAMEWORK['COMPRESSION'] has unknown keys 'LEVEL'" in message.msg

    @pytest.mark.parametrize(
        "backends",
        [
            pytest.param(["next.compression.GzipBackend"], id="bare-path"),
            pytest.param([{"OPTIONS": {}}], id="no-backend"),
            pytest.param([{"BACKEND": ""}], id="empty-path"),
            pytest.param([{"BACKEND": ZSTD, "NAME": "z"}], id="stray-key"),
            pytest.param([{"BACKEND": ZSTD, "OPTIONS": []}], id="options-list"),
            pytest.param([_entry(ZSTD, PADDING=1)], id="stray-option"),
            pytest.param([_entry(ZSTD, LEVEL=True)], id="bool-level"),
            pytest.param({"BACKEND": ZSTD}, id="one-dict"),
        ],
    )
    def test_a_misfit_backend_entry_yields_e076(self, backends) -> None:
        with _compression(BACKENDS=backends):
            (message,) = check_compression_settings()
        assert message.id == "next.E076"
        assert "NEXT_FRAMEWORK['COMPRESSION']['BACKENDS'] must be" in message.msg

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
            ("tests.support.broken.Backend", "raised RuntimeError"),
            (f"{__name__}.CustomCompression", "no subclass of CompressionBackend"),
            ("next.compression.CompressionBackend", "abstract class"),
        ],
    )
    def test_a_backend_path(self, dotted: str, problem: str) -> None:
        with _compression(BACKENDS=[*GZIP, {"BACKEND": dotted}]):
            (message,) = check_compression_paths()
        assert message.id == "next.E219"
        assert "['BACKENDS'][1]['BACKEND']" in message.msg
        assert problem in message.msg

    @pytest.mark.parametrize(
        ("dotted", "problem"),
        [
            (f"{__name__}.missing", "does not import"),
            ("tests.support.broken.exclude", "raised RuntimeError"),
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

    def test_a_default_codec_without_its_library_is_left_out_quietly(self) -> None:
        assert check_compression_codecs() == []

    @pytest.mark.parametrize(
        "scope",
        [
            pytest.param({"ORDER": ["zstd", "gzip"]}, id="order"),
            pytest.param({"BACKENDS": [{"BACKEND": ZSTD}, *GZIP]}, id="backends"),
        ],
    )
    def test_a_named_zstd_without_its_library_is_refused(self, scope) -> None:
        with _compression(**scope):
            (message,) = check_compression_codecs()
        assert message.id == "next.E228"
        assert "names the 'zstd' codec" in message.msg
        assert "pip install 'next.dj[zstd]'" in message.hint

    def test_backends_without_zstd_name_nothing(self) -> None:
        with _compression(
            BACKENDS=[{"BACKEND": "missing.Backend"}, *GZIP], ORDER=["gzip"]
        ):
            assert check_compression_codecs() == []

    def test_a_codec_of_no_extra_without_its_library_is_refused(self) -> None:
        with _compression(
            BACKENDS=[{"BACKEND": f"{__name__}.Unshipped"}, *GZIP], ORDER=["lz", "gzip"]
        ):
            (message,) = check_compression_codecs()
        assert "names the 'lz' codec" in message.msg
        assert "Install the library of the codec" in message.hint

    def test_a_missing_optional_codec_needs_a_written_order(self, monkeypatch) -> None:
        install(monkeypatch, zstandard=fake_zstandard())
        assert check_compression_codecs() == []
        with _compression(ORDER=["br", "gzip"]):
            (message,) = check_compression_codecs()
        assert "names the 'br' codec" in message.msg
        assert "pip install 'next.dj[brotli]'" in message.hint

    def test_disabled_skips_the_libraries(self) -> None:
        with _compression(ENABLED=False, SSE_TOPICS="reset", ORDER=["br"]):
            assert check_compression_codecs() == []

    def test_unknown_codings(self, monkeypatch) -> None:
        install(monkeypatch, zstandard=fake_zstandard(), brotli=fake_brotli())
        with _compression(ORDER=["gzip", "deflate", "deflate"]):
            (message,) = check_compression_codecs()
        assert message.id == "next.E228"
        assert "['ORDER'] names the coding 'deflate'" in message.msg
        assert "br, gzip, zstd" in message.hint

    def test_no_backend_at_all(self) -> None:
        with _compression(
            BACKENDS=[{"BACKEND": "next.compression.Missing"}], ORDER=["gzip"]
        ):
            (message,) = check_compression_codecs()
        assert "(none)" in message.hint

    def test_levels_and_windows_out_of_range(self, monkeypatch) -> None:
        install(monkeypatch, zstandard=fake_zstandard(), brotli=fake_brotli())
        with _compression(
            BACKENDS=[
                _entry("next.compression.GzipBackend", LEVEL=10, WINDOW=15),
                _entry(BROTLI, LEVEL=11, WINDOW=25),
                _entry(ZSTD, LEVEL=0, WINDOW=20),
            ]
        ):
            messages = check_compression_codecs()
        texts = [message.msg for message in messages]
        assert _ids(messages) == ["next.E228"] * 4
        assert "[0]['OPTIONS']['LEVEL'] is 10, outside 1 to 9" in texts[0]
        assert "the 'gzip' codec keeps its level 4" in texts[0]
        assert (
            "[0]['OPTIONS']['WINDOW'] is 15, while the 'gzip' codec takes no"
            in (texts[1])
        )
        assert (
            "[1]['OPTIONS']['WINDOW'] is 25, while the 'br' codec takes 10 to 24"
            in (texts[2])
        )
        assert "[2]['OPTIONS']['LEVEL'] is 0, outside 1 to 22" in texts[3]

    def test_a_zstd_window_past_what_browsers_decode_is_refused(
        self, monkeypatch
    ) -> None:
        install(monkeypatch, zstandard=fake_zstandard())
        with _compression(BACKENDS=[_entry(ZSTD, WINDOW=24), *GZIP]):
            (message,) = check_compression_codecs()
        assert "['WINDOW'] is 24, while the 'zstd' codec takes 10 to 23" in message.msg

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

    def test_a_missing_middleware_is_silent(self) -> None:
        with override_settings(MIDDLEWARE=[SECURITY, GZIP_MIDDLEWARE]):
            assert check_compression_middleware() == []

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


class TestStreamMiddleware:
    """`check_stream_middleware` warns when streams go to a missing middleware."""

    def test_streams_left_to_a_missing_middleware_yield_w193(self) -> None:
        with (
            override_settings(MIDDLEWARE=[SECURITY, GZIP_MIDDLEWARE]),
            _compression(STREAMS="middleware"),
        ):
            (message,) = check_stream_middleware()
        assert message.id == "next.W193"
        assert "['STREAMS'] is 'middleware'" in message.msg
        assert MIDDLEWARE in message.hint

    @pytest.mark.parametrize(
        ("middleware", "scope"),
        [
            pytest.param([SECURITY], {}, id="self"),
            pytest.param(
                [SECURITY, MIDDLEWARE], {"STREAMS": "middleware"}, id="listed"
            ),
            pytest.param(
                [f"{__name__}.Subclassed"], {"STREAMS": "middleware"}, id="subclass"
            ),
            pytest.param(
                [], {"STREAMS": "middleware", "ENABLED": False}, id="disabled"
            ),
        ],
    )
    def test_silent_otherwise(self, middleware, scope) -> None:
        with override_settings(MIDDLEWARE=middleware), _compression(**scope):
            assert check_stream_middleware() == []


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


class TestCompressDeclarations:
    """`next.E245` names a `page.compress()` call made outside a `page.py`."""

    @pytest.fixture(autouse=True)
    def _fresh(self) -> Iterator[None]:
        page_declarations.reset()
        yield
        page_declarations.reset()

    def test_a_page_call_is_silent(self, tmp_path: Path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import page\n\npage.compress(enabled=False)\n"
        )
        load_python_module_memo(page_file)
        assert check_compress_declarations() == []

    def test_a_misplaced_call(self, tmp_path: Path) -> None:
        helper = tmp_path / "helpers.py"
        helper.write_text(
            "from next.pages import page\n\npage.compress(enabled=False)\n"
        )
        load_python_module_memo(helper)
        [found] = check_compress_declarations()
        assert found.id == "next.E245"
        assert found.msg == (
            f"page.compress() in {helper} declares nothing, since the call sits "
            "outside a page.py and so names no page."
        )


def test_the_checks_run_under_the_next_tag() -> None:
    with (
        override_settings(MIDDLEWARE=[COMMON, MIDDLEWARE, GZIP_MIDDLEWARE]),
        _compression(ORDER=["zstd", "gzip"]),
    ):
        found = set(_ids(run_checks(tags=[NEXT])))
    with override_settings(MIDDLEWARE=[]), _compression(STREAMS="middleware"):
        found.update(_ids(run_checks(tags=[NEXT])))
    assert {"next.E227", "next.E228", "next.W165", "next.W193"} <= found
