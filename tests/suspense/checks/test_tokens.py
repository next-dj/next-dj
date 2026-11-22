from pathlib import Path
from unittest.mock import patch

import pytest
from django.apps import apps
from django.core.checks.registry import registry
from django.test import override_settings

from next.suspense.checks import (
    check_defer_tokens,
    check_token_binding,
    check_token_cache,
    check_token_keys,
    tokens as tokens_module,
)
from next.suspense.tokens import SignedSeal
from next.testing import override_next_settings
from tests.support import check_ids
from tests.support.crypto import install_crypto, install_msgpack


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


class Foreign:
    """A token backend path the shipped checks leave alone."""


class Unavailable(SignedSeal):
    """A seal of the project whose library never imports."""

    @classmethod
    def available(cls) -> bool:
        return False


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
        with override_next_settings(DEFER=_tokens(SEAL="next.suspense.EncryptedSeal")):
            (message,) = check_defer_tokens()
        assert message.id == "next.E230"
        assert "names EncryptedSeal, whose library does not import" in message.msg
        assert "next-dj[crypto]" in (message.hint or "")

    def test_a_seal_of_the_project_answers_through_available(self) -> None:
        assert _token_ids(SEAL=f"{__name__}.Unavailable") == ["next.E230"]

    def test_a_msgpack_codec_without_its_library_reports_e237(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_msgpack(monkeypatch)
        assert _token_ids(CODEC="next.suspense.MsgpackCodec") == []
        install_msgpack(monkeypatch, present=False)
        with override_next_settings(DEFER=_tokens(CODEC="next.suspense.MsgpackCodec")):
            (message,) = check_defer_tokens()
        assert message.id == "next.E237"
        assert "names MsgpackCodec, whose library does not import" in message.msg
        assert "msgpack for MsgpackCodec" in (message.hint or "")

    @pytest.mark.parametrize("codec", ["nowhere.Codec", "builtins.object"])
    def test_a_codec_path_naming_no_codec_is_left_to_e219(self, codec: str) -> None:
        assert _token_ids(CODEC=codec) == []

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
        monkeypatch.setattr(tokens_module, "shared_page_paths", lambda: tuple(pages))
        monkeypatch.setattr(
            tokens_module,
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
            tokens_module, "shared_page_paths", lambda: (Path("/p/page.py"),)
        )
        monkeypatch.setattr(
            tokens_module,
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
        monkeypatch.setattr(tokens_module, "mints_tokens", lambda: True)
        with override_settings(SECRET_KEY=secret, SECRET_KEY_FALLBACKS=fallbacks):
            (message,) = check_token_keys()
        assert message.id == "next.E231"
        assert "forge" in message.msg
        assert secret not in message.msg

    def test_a_project_that_mints_no_token_keeps_a_short_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tokens_module, "mints_tokens", lambda: False)
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


class TestCheckTokenBinding:
    """``check_token_binding`` warns on deploy when accounts meet unbound tokens."""

    @pytest.fixture()
    def minting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tokens_module, "mints_tokens", lambda: True)

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
        monkeypatch.setattr(tokens_module, "mints_tokens", lambda: False)
        assert check_token_binding() == []

    def test_the_binding_check_runs_on_deploy_only(self) -> None:
        deployed = registry.get_checks(include_deployment_checks=True)
        assert check_token_binding in deployed
        assert check_token_binding not in registry.get_checks()
