from pathlib import Path
from unittest.mock import patch

import django
import pytest
from django.apps import apps
from django.core.checks.registry import registry
from django.test import override_settings

from next.components.info import ComponentInfo
from next.pages.loaders import forget_layout_texts
from next.suspense import checks as checks_module
from next.suspense.checks import (
    check_buffering_gzip,
    check_bundles_have_a_runtime,
    check_connection_sources,
    check_defer_backends,
    check_defer_tokens,
    check_durable_jobs,
    check_pool_budget,
    check_pool_timeout,
    check_stream_nonce,
    check_thread_sources,
    check_token_binding,
    check_token_cache,
    check_token_keys,
)
from next.suspense.connections import ConnectionState
from next.testing import override_next_settings
from tests.support import check_ids, patch_checks_router_manager, write_page
from tests.support.access import PAGE_HEAD
from tests.support.checks import run_check
from tests.support.crypto import install_crypto


_LOCMEM = "django.core.cache.backends.locmem.LocMemCache"
_DUMMY = "django.core.cache.backends.dummy.DummyCache"
_REDIS = "django.core.cache.backends.redis.RedisCache"
_CACHE_STORAGE = "next.suspense.CacheStorage"
_LONG = "s" * 50


def tenant(request) -> str | None:
    return None


def _tokens(**options: object) -> dict[str, object]:
    return {"TOKENS": {"OPTIONS": options}}


def _token_ids(**options: object) -> list[str]:
    with override_next_settings(DEFER=_tokens(**options)):
        return check_ids(check_defer_tokens())


def _messages(defer: dict[str, object]) -> list[str]:
    with override_next_settings(DEFER=defer):
        return [message.msg for message in check_defer_backends()]


class TestCheckDeferBackends:
    """``check_defer_backends`` reports an unusable backend path as ``next.E219``."""

    def test_the_defaults_report_nothing(self) -> None:
        assert check_defer_backends() == []

    def test_settings_without_a_defer_scope_report_nothing(self) -> None:
        with override_settings(NEXT_FRAMEWORK={}):
            assert check_defer_backends() == []

    @pytest.mark.parametrize(
        "defer",
        [
            {"EXECUTOR": {"BACKEND": "next.suspense.LaneExecutor"}},
            {"ADMISSION": "next.suspense.QueueDelay"},
            {"EXECUTOR": "not a mapping", "ADMISSION": 3},
            {"EXECUTOR": {"BACKEND": ""}},
            {"EXECUTOR": {"OPTIONS": {"MAX_WORKERS": 2}}},
        ],
    )
    def test_usable_or_misshaped_values_report_nothing(
        self, defer: dict[str, object]
    ) -> None:
        with override_next_settings(DEFER=defer):
            assert check_defer_backends() == []

    def test_a_path_that_does_not_import_reports_e219(self) -> None:
        with override_next_settings(DEFER={"EXECUTOR": {"BACKEND": "nowhere.Pool"}}):
            messages = check_defer_backends()
        assert check_ids(messages) == ["next.E219"]
        assert (
            "['EXECUTOR']['BACKEND'] 'nowhere.Pool' does not import" in messages[0].msg
        )
        assert "'next.suspense.LaneExecutor'" in (messages[0].hint or "")

    @pytest.mark.parametrize("path", ["builtins.object", "os.path.join"])
    def test_a_path_outside_the_family_reports_e219(self, path: str) -> None:
        (message,) = _messages({"ADMISSION": path})
        assert f"['ADMISSION'] {path!r} is no subclass of Admission" in message

    def test_abstract_roots_report_e219_for_each_family(self) -> None:
        defer = {
            "EXECUTOR": {"BACKEND": "next.suspense.BoundaryExecutor"},
            "ADMISSION": "next.suspense.Admission",
            "POLICY": {"BACKEND": "next.suspense.StreamPolicy"},
            "TRANSPORT": {"BACKEND": "next.suspense.StreamTransport"},
        }
        messages = _messages(defer)
        assert len(messages) == 4
        assert "['POLICY']['BACKEND']" in messages[2]
        assert "['TRANSPORT']['BACKEND']" in messages[3]
        assert all("is an abstract class" in message for message in messages)


def test_the_source_checks_are_registered() -> None:
    registered = set(registry.get_checks())
    assert {
        check_buffering_gzip,
        check_connection_sources,
        check_durable_jobs,
        check_pool_budget,
        check_pool_timeout,
        check_stream_nonce,
        check_thread_sources,
    } <= registered


class TestTokenPaths:
    """``check_defer_backends`` reports token paths that name nothing as ``next.E219``."""

    def test_a_token_backend_that_does_not_import_reports_e219(self) -> None:
        (message,) = _messages({"TOKENS": {"BACKEND": "nowhere.Tokens"}})
        assert "['TOKENS']['BACKEND'] 'nowhere.Tokens' does not import" in message

    @pytest.mark.parametrize(
        ("key", "path", "words"),
        [
            ("CODEC", "nowhere.Codec", "does not import"),
            ("STORAGE", "next.suspense.JsonCodec", "is no subclass of TokenStorage"),
            ("SEAL", "next.suspense.TokenSeal", "is an abstract class"),
            ("TRANSPORT", "builtins.int", "is no subclass of TokenTransport"),
            ("KEYS", "nowhere.keys", "does not import"),
            ("MAX_AGE", "next.suspense.tokens.claims.VERSION", "names no callable"),
            ("BINDING", "nowhere.binding", "does not import"),
            ("BINDING", "next.suspense.tokens.claims.VERSION", "names no callable"),
            ("BINDING", "builtins.int", "is no subclass of SubjectBinding"),
            ("BINDING", "next.identity.SubjectBinding", "is an abstract class"),
            ("BINDING", "next.suspense.CallableBinding", "takes arguments"),
        ],
    )
    def test_an_unusable_option_reports_e219(
        self, key: str, path: str, words: str
    ) -> None:
        (message,) = _messages(_tokens(**{key: path}))
        assert f"['TOKENS']['OPTIONS'][{key!r}] {path!r} {words}" in message

    def test_an_unusable_binding_hints_at_every_accepted_form(self) -> None:
        with override_next_settings(
            DEFER=_tokens(BINDING="next.suspense.CallableBinding")
        ):
            (message,) = check_defer_backends()
        assert message.id == "next.E219"
        assert "SubjectBinding subclass that takes no arguments" in (message.hint or "")
        assert "request callable" in (message.hint or "")

    @pytest.mark.parametrize(
        ("key", "path"),
        [
            ("BINDING", "session"),
            ("BINDING", "next.suspense.UserBinding"),
            ("BINDING", f"{__name__}.tenant"),
            ("KEYS", f"{__name__}.tenant"),
            ("MAX_AGE", f"{__name__}.tenant"),
            ("MAX_AGE", 60),
            ("SEAL", "next.suspense.SignedSeal"),
        ],
    )
    def test_a_usable_option_reports_nothing(self, key: str, path: object) -> None:
        assert _messages(_tokens(**{key: path})) == []

    def test_a_foreign_backend_owns_its_options(self) -> None:
        defer = {
            "TOKENS": {
                "BACKEND": "next.suspense.StackedTokenBackend",
                "OPTIONS": {"CODEC": "nowhere.Codec"},
            }
        }
        assert len(_messages(defer)) == 1
        defer["TOKENS"]["BACKEND"] = f"{__name__}.Foreign"
        assert all("Foreign" in message for message in _messages(defer))

    def test_a_tokens_value_that_is_no_mapping_reads_the_shipped_backend(self) -> None:
        assert _messages({"TOKENS": "nope"}) == []


class TestCheckDeferTokens:
    """``check_defer_tokens`` reports token seams that forge, fail or expire."""

    def test_the_defaults_report_nothing(self) -> None:
        assert check_defer_tokens() == []

    def test_no_seal_outside_debug_reports_e221(self, settings) -> None:
        settings.DEBUG = False
        assert _token_ids(SEAL="next.suspense.NoSeal") == ["next.E221"]
        settings.DEBUG = True
        assert _token_ids(SEAL="next.suspense.NoSeal") == []

    def test_a_session_binding_without_sessions_reports_e226(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert _token_ids(BINDING="next.suspense.SessionBinding") == []
        monkeypatch.setattr(apps, "is_installed", lambda *args: False)
        assert _token_ids(BINDING="session") == ["next.E226"]

    @pytest.mark.parametrize(
        "binding", ["next.realtime.SessionBinding", "next.realtime.SignedCookieBinding"]
    )
    def test_any_binding_that_reads_the_session_reports_e226(
        self, monkeypatch: pytest.MonkeyPatch, binding: str
    ) -> None:
        monkeypatch.setattr(apps, "is_installed", lambda *args: False)
        with override_next_settings(DEFER=_tokens(BINDING=binding)):
            (message,) = check_defer_tokens()
        assert message.id == "next.E226"
        assert binding.rsplit(".", 1)[1] in message.msg

    @pytest.mark.parametrize(
        "binding", ["user", "none", f"{__name__}.tenant", "builtins.object"]
    )
    def test_a_binding_that_reads_no_session_reports_no_e226(
        self, monkeypatch: pytest.MonkeyPatch, binding: str
    ) -> None:
        monkeypatch.setattr(apps, "is_installed", lambda *args: False)
        assert _token_ids(BINDING=binding) == []

    def test_an_encrypted_seal_without_its_library_reports_e230(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_crypto(monkeypatch)
        assert _token_ids(SEAL="next.suspense.EncryptedSeal") == []
        install_crypto(monkeypatch, present=False)
        assert _token_ids(SEAL="next.suspense.EncryptedSeal") == ["next.E230"]

    @pytest.mark.parametrize(
        "engine",
        [
            "django.contrib.sessions.backends.cache",
            "django.contrib.sessions.backends.cached_db",
        ],
    )
    def test_a_cache_shared_with_sessions_reports_e234(self, settings, engine) -> None:
        settings.SESSION_ENGINE = engine
        settings.SESSION_CACHE_ALIAS = "default"
        storage = _CACHE_STORAGE
        assert _token_ids(STORAGE=storage) == ["next.E234", "next.W180"]
        assert _token_ids(STORAGE="next.suspense.SpillStorage") == [
            "next.E234",
            "next.W180",
        ]
        assert _token_ids(STORAGE=storage, CACHE_ALIAS="default") == ["next.E234"]
        assert _token_ids(STORAGE=storage, CACHE_ALIAS="tokens") == []
        assert _token_ids(STORAGE="next.suspense.InlineStorage") == []

    def test_a_database_session_engine_reports_no_e234(self, settings) -> None:
        settings.SESSION_ENGINE = "django.contrib.sessions.backends.db"
        assert _token_ids(STORAGE=_CACHE_STORAGE, CACHE_ALIAS="default") == []

    @pytest.mark.parametrize(
        ("tokens", "sessions", "shared"),
        [
            ({"BACKEND": _LOCMEM}, {"BACKEND": _LOCMEM}, True),
            (
                {"BACKEND": _LOCMEM, "LOCATION": "a"},
                {"BACKEND": _LOCMEM, "LOCATION": "a"},
                True,
            ),
            (
                {"BACKEND": _LOCMEM, "LOCATION": "a"},
                {"BACKEND": _LOCMEM, "LOCATION": "b"},
                False,
            ),
            (
                {"BACKEND": _REDIS, "LOCATION": "redis://h2; redis://h1"},
                {"BACKEND": _REDIS, "LOCATION": ["redis://h1", "redis://h2"]},
                True,
            ),
            (
                {"BACKEND": _REDIS, "LOCATION": "redis://h1"},
                {"BACKEND": _LOCMEM, "LOCATION": "redis://h1"},
                False,
            ),
            (
                {"BACKEND": "nowhere.Cache", "LOCATION": 7},
                {"BACKEND": "nowhere.Cache", "LOCATION": 7},
                True,
            ),
            ({"LOCATION": "a"}, {"LOCATION": "a"}, True),
            (None, {"BACKEND": _LOCMEM}, False),
        ],
    )
    def test_two_aliases_on_one_store_report_e234(
        self, settings, tokens, sessions, shared
    ) -> None:
        caches = {"default": sessions}
        if tokens is not None:
            caches["tokens"] = tokens
        settings.CACHES = caches
        settings.SESSION_ENGINE = "django.contrib.sessions.backends.cache"
        settings.SESSION_CACHE_ALIAS = "default"
        found = _token_ids(STORAGE=_CACHE_STORAGE, CACHE_ALIAS="tokens")
        assert found == (["next.E234"] if shared else [])

    def test_claims_in_a_cache_nobody_chose_warn_w180(self, settings) -> None:
        settings.SESSION_ENGINE = "django.contrib.sessions.backends.db"
        with override_next_settings(DEFER=_tokens(STORAGE=_CACHE_STORAGE)):
            (message,) = check_defer_tokens()
        assert message.id == "next.W180"
        assert "unpickles" in message.msg
        assert "session cache" in (message.hint or "")
        assert _token_ids(STORAGE="next.suspense.SpillStorage") == ["next.W180"]
        assert _token_ids(STORAGE=_CACHE_STORAGE, CACHE_ALIAS="tokens") == []
        assert _token_ids(STORAGE="next.suspense.InlineStorage") == []

    def test_a_bucket_with_an_encrypting_seal_warns_w174(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_crypto(monkeypatch)
        seal = "next.suspense.EncryptedSeal"
        assert _token_ids(SEAL=seal, BUCKET=60) == ["next.W174"]
        assert _token_ids(SEAL=seal) == []
        assert _token_ids(BUCKET=60) == []

    def test_a_seal_path_that_does_not_import_is_left_to_e219(self) -> None:
        assert _token_ids(SEAL="nowhere.Seal", BUCKET=60) == []

    def test_a_foreign_backend_is_not_read(self) -> None:
        defer = {"TOKENS": {"BACKEND": f"{__name__}.Foreign"}}
        with override_next_settings(DEFER=defer):
            assert check_defer_tokens() == []

    def test_a_shell_cached_past_the_token_lifetime_warns_w167(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pages = [Path("/pages/long/page.py"), Path("/pages/short/page.py")]
        lifetimes = {pages[0]: 600, pages[1]: 300}
        monkeypatch.setattr(checks_module, "shared_page_paths", lambda: tuple(pages))
        monkeypatch.setattr(
            checks_module,
            "load_python_module_memo",
            lambda path: type("Module", (), {"cache": lifetimes[path]}),
        )
        with override_next_settings(DEFER=_tokens(MAX_AGE=600, BUCKET=60)):
            (message,) = check_defer_tokens()
        assert message.id == "next.W167"
        assert "/pages/long/page.py" in message.msg
        assert "/pages/short/page.py" not in message.msg
        assert _token_ids(MAX_AGE=660, BUCKET=60) == []
        assert _token_ids(MAX_AGE=f"{__name__}.tenant") == []

    def test_a_page_without_a_static_lifetime_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            checks_module, "shared_page_paths", lambda: (Path("/p/page.py"),)
        )
        monkeypatch.setattr(
            checks_module,
            "load_python_module_memo",
            lambda *args: type("Module", (), {"cache": {"public": True}}),
        )
        assert _token_ids(MAX_AGE=1) == []


class TestCheckTokenKeys:
    """``check_token_keys`` refuses a short project secret the default ring signs with."""

    def test_long_project_secrets_report_nothing(self) -> None:
        with override_settings(SECRET_KEY=_LONG, SECRET_KEY_FALLBACKS=[_LONG]):
            assert check_token_keys() == []

    @pytest.mark.parametrize(
        ("secret", "fallbacks"), [("s" * 31, []), (_LONG, ["a"]), (_LONG, [_LONG, 7])]
    )
    def test_a_short_project_secret_reports_e233(
        self, secret: str, fallbacks: list[object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(checks_module, "mints_tokens", lambda: True)
        with override_settings(SECRET_KEY=secret, SECRET_KEY_FALLBACKS=fallbacks):
            (message,) = check_token_keys()
        assert message.id == "next.E231"
        assert "forge" in message.msg
        assert secret not in message.msg

    def test_a_project_that_mints_no_token_keeps_a_short_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(checks_module, "mints_tokens", lambda: False)
        with override_settings(SECRET_KEY="short", SECRET_KEY_FALLBACKS=["a"]):
            assert check_token_keys() == []

    def test_a_ring_of_its_own_leaves_the_secrets_alone(self) -> None:
        with (
            override_settings(SECRET_KEY="short", SECRET_KEY_FALLBACKS=[]),
            override_next_settings(DEFER=_tokens(KEYS=["k" * 32])),
        ):
            assert check_token_keys() == []
        with (
            override_settings(SECRET_KEY="short", SECRET_KEY_FALLBACKS=[]),
            override_next_settings(DEFER=_tokens(KEYS="myapp.secrets.ring")),
        ):
            assert check_token_keys() == []

    def test_a_foreign_backend_is_not_read(self) -> None:
        defer = {"TOKENS": {"BACKEND": f"{__name__}.Foreign"}}
        with (
            override_settings(SECRET_KEY="short", SECRET_KEY_FALLBACKS=[]),
            override_next_settings(DEFER=defer),
        ):
            assert check_token_keys() == []

    def test_an_empty_secret_is_left_to_django(self) -> None:
        with override_settings(SECRET_KEY=""):
            assert check_token_keys() == []

    def test_the_key_check_runs_at_startup(self) -> None:
        assert check_token_keys in registry.get_checks()


class TestCheckTokenCache:
    """``check_token_cache`` refuses a process-local token cache on deploy."""

    def test_a_locmem_token_cache_reports_e223(self, settings) -> None:
        settings.CACHES = {"default": {"BACKEND": _LOCMEM}}
        with override_next_settings(
            DEFER=_tokens(STORAGE="next.suspense.CacheStorage")
        ):
            assert check_ids(check_token_cache()) == ["next.E223"]

    @pytest.mark.parametrize(
        "caches",
        [
            {"default": {"BACKEND": _DUMMY}},
            {"default": {"BACKEND": "nowhere.Cache"}},
            {"default": {}},
            {"other": {"BACKEND": _LOCMEM}},
        ],
    )
    def test_a_shared_or_unknown_cache_reports_nothing(self, settings, caches) -> None:
        settings.CACHES = caches
        with override_next_settings(
            DEFER=_tokens(STORAGE="next.suspense.CacheStorage")
        ):
            assert check_token_cache() == []

    def test_inline_storage_reports_nothing(self) -> None:
        assert check_token_cache() == []

    def test_the_cache_check_runs_on_deploy_only(self) -> None:
        assert check_token_cache in registry.get_checks(include_deployment_checks=True)
        assert check_token_cache not in registry.get_checks()
        assert check_defer_tokens in registry.get_checks()


class Foreign:
    """A token backend path the shipped checks leave alone."""


DISABLED = {"policy": "disabled"}
LOADING_ZONE = '{% zone "rows" reload="loading" %}<p>r</p>{% endzone %}'
WINDOWED_MORE = (
    '{% zone "rows" %}<p>r</p>{% endzone %}'
    '{% more zone="rows" next=n prune=10 %}More{% endmore %}'
)


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


@pytest.fixture()
def no_tokens():
    with patch.object(checks_module, "mints_tokens", return_value=False):
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

        with patch.object(checks_module, "mints_tokens", return_value=True):
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
        with patch.object(checks_module, "next_framework_settings") as framework:
            framework.NEXT_JS_OPTIONS = []
            assert check_bundles_have_a_runtime() == []


_GZIP = "django.middleware.gzip.GZipMiddleware"
_DJANGO_CSP = "django.middleware.csp.ContentSecurityPolicyMiddleware"
_NO_STREAM = {"POLICY": {"OPTIONS": {"STREAM": False}}}
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

    def test_next_compression_keeps_it_quiet(self) -> None:
        with (
            patch.object(django, "VERSION", _DJANGO_60),
            override_settings(MIDDLEWARE=[_GZIP]),
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


_DURABLE_PAGE = (
    "from django.db import transaction\n"
    "from next.pages import context\n\n"
    "@context('rows')\n"
    "def rows():\n"
    "    with transaction.atomic(durable=True):\n"
    "        return 1\n\n"
    "template = 'ok'\n"
)
_STATE = {"CONNECTION_STATE": ["tests.suspense.test_checks.Replayed"]}


class Replayed(ConnectionState):
    """A connection state backend that replays nothing."""

    def capture(self, request: object, alias: str) -> None:
        return None

    def apply(self, state: object, connection: object) -> None:
        return None


def _component(root: Path, name: str, source: str | None) -> ComponentInfo:
    folder = root / name
    folder.mkdir()
    module_path = None
    if source is not None:
        module_path = folder / "component.py"
        module_path.write_text(source)
    return ComponentInfo(name, root, "", folder / "component.djx", module_path, False)


class TestDurableJobs:
    """next.W173 flags `durable=True` in code a deferred job runs with connection state."""

    def _messages(self, *infos: ComponentInfo) -> list[object]:
        with (
            patch.object(checks_module, "deferred_components", return_value=infos),
            patch.object(checks_module, "load_routed_pages", return_value=[]),
            override_next_settings(DEFER=_STATE),
        ):
            return check_durable_jobs()

    def test_a_component_passing_durable_is_flagged(self, tmp_path: Path) -> None:
        info = _component(tmp_path, "card", "atomic(durable=True)\n")
        [message] = self._messages(info)
        assert message.id == "next.W173"
        assert info.module_path is not None
        assert message.obj == str(info.module_path.resolve())
        assert "CONNECTION_STATE" in message.msg

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("atomic(durable=False)\n", id="not-durable"),
            pytest.param("atomic(durable=flag)\n", id="unknown-value"),
            pytest.param("def broken(:\n", id="syntax-error"),
            pytest.param(None, id="no-module"),
        ],
    )
    def test_anything_else_is_quiet(self, tmp_path: Path, source: str | None) -> None:
        assert self._messages(_component(tmp_path, "card", source)) == []

    def test_an_unreadable_module_is_skipped(self, tmp_path: Path) -> None:
        info = _component(tmp_path, "card", "atomic(durable=True)\n")
        assert info.module_path is not None
        info.module_path.unlink()
        assert self._messages(info) == []

    def test_no_connection_state_is_quiet(self, tmp_path: Path) -> None:
        info = _component(tmp_path, "card", "atomic(durable=True)\n")
        with patch.object(checks_module, "deferred_components", return_value=[info]):
            assert check_durable_jobs() == []

    def test_a_page_with_a_boundary_is_scanned(self, tmp_path: Path) -> None:
        (tmp_path / "layout.djx").write_text("<html>{% template %}</html>")
        (tmp_path / "shop").mkdir()
        (tmp_path / "shop" / "loading.djx").write_text("<p>loading</p>")
        forget_layout_texts()
        page_file = write_page(tmp_path, "shop", _DURABLE_PAGE)
        write_page(tmp_path, "plain", _DURABLE_PAGE)
        with (
            patch_checks_router_manager(pages_directory=tmp_path),
            patch.object(checks_module, "deferred_components", return_value=[]),
            override_next_settings(DEFER=_STATE),
        ):
            messages = check_durable_jobs()
        assert [(m.id, m.obj) for m in messages] == [
            ("next.W173", str(page_file.resolve()))
        ]

    def test_a_callable_without_code_has_no_source(self) -> None:
        assert checks_module._source_file(len) is None


class TestCheckTokenBinding:
    """``check_token_binding`` warns on deploy when accounts meet unbound tokens."""

    @pytest.fixture()
    def minting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(checks_module, "mints_tokens", lambda: True)

    @pytest.mark.usefixtures("minting")
    def test_the_default_binding_with_auth_installed_reports_w186(self) -> None:
        (message,) = check_token_binding()
        assert message.id == "next.W186"
        assert "is NoBinding in a project with django.contrib.auth" in message.msg

    @pytest.mark.usefixtures("minting")
    @pytest.mark.parametrize(
        "binding", ["user", "session", "next.suspense.UserBinding", "tests.x.y"]
    )
    def test_a_named_binding_reports_nothing(self, binding: str) -> None:
        with override_next_settings(DEFER=_tokens(BINDING=binding)):
            assert check_token_binding() == []

    @pytest.mark.usefixtures("minting")
    def test_a_project_without_auth_reports_nothing(self) -> None:
        with patch.object(apps, "is_installed", return_value=False):
            assert check_token_binding() == []

    def test_a_project_minting_no_token_reports_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(checks_module, "mints_tokens", lambda: False)
        assert check_token_binding() == []

    def test_the_binding_check_runs_on_deploy_only(self) -> None:
        deployed = registry.get_checks(include_deployment_checks=True)
        assert check_token_binding in deployed
        assert check_token_binding not in registry.get_checks()
