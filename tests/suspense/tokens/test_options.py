import math
from dataclasses import replace

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

from next.conf.merge import FROZEN_DEFAULTS
from next.realtime import SignedCookieBinding
from next.suspense.tokens import (
    VERSION,
    NoBinding,
    SessionBinding,
    TokenSettings,
    UserBinding,
)
from tests.support.tokens import NOW, claims, request_for


_SHIPPED = dict(FROZEN_DEFAULTS["DEFER"]["TOKENS"]["OPTIONS"])


def _settings(**options: object) -> TokenSettings:
    return TokenSettings.read({**_SHIPPED, **options})


class TestRead:
    """``TokenSettings.read`` builds the numbers and the binding of the options."""

    def test_the_shipped_options_read(self) -> None:
        settings = _settings()
        assert isinstance(settings.binding, NoBinding)
        assert (settings.max_age, settings.bucket, settings.max_bytes) == (
            600,
            None,
            2048,
        )
        assert settings.max_stored_bytes == 65_536
        assert "SPILL_BYTES" not in _SHIPPED
        assert (settings.cache_alias, settings.epoch) == ("default", 1)

    @pytest.mark.parametrize(
        "options",
        [
            {"MAX_AGE": -1},
            {"MAX_AGE": 0},
            {"MAX_AGE": True},
            {"MAX_AGE": "nowhere.age"},
            {"MAX_AGE": "next.suspense.tokens.claims.VERSION"},
            {"BUCKET": -1},
            {"BUCKET": 600},
            {"MAX_BYTES": 0},
            {"EPOCH": -1},
            {"BINDING": None},
            {"CACHE_ALIAS": ""},
        ],
    )
    def test_a_misfit_is_refused(self, options: dict[str, object]) -> None:
        with pytest.raises(ImproperlyConfigured):
            _settings(**options)

    def test_a_session_binding_without_sessions_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(apps, "is_installed", lambda *args: False)
        with pytest.raises(ImproperlyConfigured, match=r"django\.contrib\.sessions"):
            _settings(BINDING="next.suspense.SessionBinding")

    def test_a_session_binding_with_sessions_reads(self) -> None:
        binding = _settings(BINDING="next.suspense.SessionBinding").binding
        assert isinstance(binding, SessionBinding)

    def test_any_binding_that_reads_the_session_needs_its_app(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert SignedCookieBinding.needs_sessions
        monkeypatch.setattr(apps, "is_installed", lambda *args: False)
        with pytest.raises(ImproperlyConfigured, match=r"django\.contrib\.sessions"):
            _settings(BINDING="next.realtime.SignedCookieBinding")
        assert isinstance(_settings(BINDING="user").binding, UserBinding)


class TestOverrides:
    """A component's ``tokens`` overrides merge over the project settings."""

    def test_no_overrides_keep_the_settings(self) -> None:
        settings = _settings()
        assert settings.overridden(()) is settings

    def test_each_override_replaces_its_value(self) -> None:
        merged = _settings(BUCKET=30).overridden(
            (("binding", "user"), ("bucket", None), ("max_age", 60), ("max_bytes", 99))
        )
        assert isinstance(merged.binding, UserBinding)
        assert (merged.bucket, merged.max_age, merged.max_bytes) == (None, 60, 99)

    def test_a_bucket_override_keeps_the_rest(self) -> None:
        merged = _settings().overridden((("bucket", 10),))
        assert (merged.bucket, merged.max_age) == (10, 600)

    @pytest.mark.parametrize(
        "tokens",
        [
            (("max_age", 60), ("bucket", 60)),
            (("max_age", -1),),
            (("max_age", 0),),
            (("max_bytes", 0),),
            (("binding", 3),),
            (("binding", "nowhere.binding"),),
            (("seal", "none"),),
        ],
    )
    def test_a_misfit_override_is_refused(self, tokens) -> None:
        with pytest.raises(ImproperlyConfigured):
            _settings().overridden(tokens)

    def test_a_session_override_without_sessions_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(apps, "is_installed", lambda *args: False)
        with pytest.raises(ImproperlyConfigured, match=r"django\.contrib\.sessions"):
            _settings().overridden((("binding", "session"),))


class TestLifetime:
    """A static ``MAX_AGE`` is used as is, a callable answer is clamped to a day."""

    def test_a_static_age_is_used_as_is(self) -> None:
        assert _settings(MAX_AGE=90_000).lifetime(request_for(), claims()) == 90_000

    @pytest.mark.parametrize(
        ("answer", "seconds"),
        [(30, 30), (0, 1), (-5, 1), (2.9, 2), (10**9, 86_400), (86_400, 86_400)],
    )
    def test_a_callable_age_is_clamped(
        self, answers, answer: object, seconds: int
    ) -> None:
        answers.age = answer
        settings = _settings(MAX_AGE=answers.age_path)
        assert settings.lifetime(request_for(), claims()) == seconds

    @pytest.mark.parametrize("answer", [None, "60", True, math.nan, math.inf])
    def test_a_callable_answer_that_is_no_number_is_refused(
        self, answers, answer
    ) -> None:
        answers.age = answer
        settings = _settings(MAX_AGE=answers.age_path)
        with pytest.raises(ImproperlyConfigured, match="not a number"):
            settings.lifetime(request_for(), claims())

    def test_a_callable_age_skips_the_bucket_rule(self, answers) -> None:
        settings = _settings(MAX_AGE=answers.age_path, BUCKET=10**6)
        assert settings.bucket == 10**6


class TestStamped:
    """A mint stamps the version, the floored second and the expiry it seals."""

    def test_the_expiry_is_the_mint_second_plus_the_lifetime(self) -> None:
        stamped = _settings(MAX_AGE=90).stamped(request_for(), claims(), NOW)
        assert (stamped.version, stamped.issued, stamped.expires) == (
            VERSION,
            NOW,
            NOW + 90,
        )

    def test_a_bucket_floors_both_seconds(self) -> None:
        stamped = _settings(BUCKET=60).stamped(request_for(), claims(), NOW + 59)
        assert (stamped.issued, stamped.expires) == (1_000_020, 1_000_620)

    @pytest.mark.parametrize("answer", [60, 300, 0])
    def test_a_bucket_that_reaches_a_callable_age_is_refused(
        self, answers, answer
    ) -> None:
        answers.age = answer
        settings = _settings(MAX_AGE=answers.age_path, BUCKET=300)
        with pytest.raises(ImproperlyConfigured, match="born expired"):
            settings.stamped(request_for(), claims(), NOW)

    def test_a_bucket_below_a_callable_age_mints(self, answers) -> None:
        answers.age = 301
        settings = _settings(MAX_AGE=answers.age_path, BUCKET=300)
        assert settings.stamped(request_for(), claims(), NOW).expires == 1_000_201


class TestAlive:
    """A redeem takes the earlier of the sealed expiry and the policy's lifetime."""

    def _sealed(self, lifetime: int):
        return replace(claims(), issued=NOW, expires=NOW + lifetime)

    def test_a_longer_policy_never_extends_the_sealed_expiry(self) -> None:
        settings = _settings(MAX_AGE=3600)
        assert settings.alive(request_for(), self._sealed(60), NOW + 59)
        assert not settings.alive(request_for(), self._sealed(60), NOW + 60)

    def test_a_shorter_policy_shortens_the_sealed_expiry(self) -> None:
        settings = _settings(MAX_AGE=30)
        assert settings.alive(request_for(), self._sealed(600), NOW + 29)
        assert not settings.alive(request_for(), self._sealed(600), NOW + 30)

    def test_a_callable_answering_longer_for_a_request_extends_nothing(
        self, answers
    ) -> None:
        answers.age = 86_400
        settings = _settings(MAX_AGE=answers.age_path)
        assert not settings.alive(request_for(), self._sealed(60), NOW + 61)

    def test_claims_without_a_sealed_expiry_are_dead(self) -> None:
        assert not _settings().alive(request_for(), replace(claims(), issued=NOW), NOW)


class TestIssued:
    @pytest.mark.parametrize(
        ("bucket", "now", "issued"),
        [
            (None, 1_000_123, 1_000_123),
            (0, 1_000_123, 1_000_123),
            (60, 1_000_123, 1_000_080),
        ],
    )
    def test_the_bucket_floors_the_mint_second(self, bucket, now, issued) -> None:
        assert _settings(BUCKET=bucket).issued(now) == issued
