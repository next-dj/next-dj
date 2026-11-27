from dataclasses import replace

import pytest
from django.contrib.auth.models import AnonymousUser

from next.defer import BoundaryTokenError, TokenRejection
from next.defer.tokens import VERSION, SignedSeal
from next.defer.tokens.seal import token_salt
from next.defer.tokens.settings import PROJECT_PROFILE, override_profile
from next.signals import boundary_token_rejected
from next.testing import capture_signals
from next.testing.tokens import FrozenClock, clocked
from tests.support.tokens import (
    NOW,
    MinimalTokenBackend,
    claims,
    request_for,
    stacked,
    target,
)


_AGES: list[int] = []


def age_of(request, claims) -> int:
    return _AGES[-1]


def _reason(call) -> TokenRejection:
    with pytest.raises(BoundaryTokenError) as caught:
        call()
    return caught.value.reason


class TestContract:
    """A backend implements `issue`, `open` and `settle`, and inherits the rest."""

    def test_the_inherited_redeem_opens_settles_and_announces(self) -> None:
        with capture_signals(boundary_token_rejected) as recorder:
            reason = _reason(
                lambda: MinimalTokenBackend().redeem(request_for(), "t", target())
            )
        assert reason is TokenRejection.MALFORMED
        (event,) = recorder
        assert event.sender is MinimalTokenBackend
        assert event.kwargs["component"] == "shop/card"

    def test_the_inherited_announce_sends_the_signal(self) -> None:
        error = BoundaryTokenError(TokenRejection.MALFORMED)
        with capture_signals(boundary_token_rejected) as recorder:
            MinimalTokenBackend().announce(request_for(), error, "card")
        (event,) = recorder
        assert event.kwargs["reason"] is TokenRejection.MALFORMED
        assert event.kwargs["component"] == "card"

    def test_the_inherited_backend_binds_the_visitor(self) -> None:
        assert MinimalTokenBackend().bound(request_for())

    def test_the_inherited_backend_names_no_profile(self) -> None:
        assert MinimalTokenBackend().profile("token") is None


class TestOpen:
    """`open` verifies the seal, epoch, version and storage, comparing no claim."""

    def test_a_minted_token_opens_to_its_claims(self) -> None:
        backend = stacked()
        token = backend.issue(request_for(), claims(sku="A1"))
        opened = backend.open(request_for("/elsewhere/"), token)
        assert opened.component == "shop/card"
        assert opened.url_path == "/shop/"

    def test_a_token_over_the_size_of_the_overrides_is_too_large(self) -> None:
        backend = stacked()
        token = backend.issue(request_for(), claims())
        reason = _reason(
            lambda: backend.open(request_for(), token, tokens=(("max_bytes", 8),))
        )
        assert reason is TokenRejection.TOO_LARGE

    def test_a_token_of_another_shape_is_malformed(self) -> None:
        assert _reason(lambda: stacked().open(request_for(), "x")) is (
            TokenRejection.MALFORMED
        )

    def test_a_visitor_the_binding_cannot_name_is_refused(self) -> None:
        backend = stacked(BINDING="user")
        request = request_for()
        request.user = AnonymousUser()
        token = backend.issue(request, claims())
        assert _reason(lambda: backend.open(request_for(), token)) is (
            TokenRejection.BINDING
        )

    def test_a_token_of_a_past_epoch_is_expired(self) -> None:
        token = stacked(EPOCH=1).issue(request_for(), claims())
        assert _reason(lambda: stacked(EPOCH=2).open(request_for(), token)) is (
            TokenRejection.EXPIRED
        )

    def test_a_verified_unknown_version_is_refused_as_version(self) -> None:
        salt = token_salt(VERSION + 1, 1, PROJECT_PROFILE, "")
        token = f"{VERSION + 1}.1.0.{SignedSeal().seal(b'i{}', salt)}"
        assert _reason(lambda: stacked().open(request_for(), token)) is (
            TokenRejection.VERSION
        )

    @pytest.mark.parametrize(
        ("index", "value"), [(0, "9"), (1, "7"), (2, "0" * 12), (2, "1" * 12)]
    )
    def test_an_edited_header_fails_the_seal(self, index: int, value: str) -> None:
        backend = stacked(EPOCH=1)
        parts = backend.issue(request_for(), claims()).split(".", 3)
        parts[index] = value
        token = ".".join(parts)
        assert _reason(lambda: backend.open(request_for(), token)) is (
            TokenRejection.SIGNATURE
        )

    def test_the_header_names_the_profile_of_the_overrides(self) -> None:
        backend = stacked()
        overrides = (("max_age", 60),)
        plain = backend.issue(request_for(), claims())
        named = backend.issue(request_for(), claims(), tokens=overrides)
        assert plain.split(".")[2] == PROJECT_PROFILE
        assert backend.profile(plain) == PROJECT_PROFILE
        assert backend.profile(named) == override_profile(overrides)
        assert len(override_profile(overrides)) == 12
        assert backend.profile("x") == PROJECT_PROFILE

    def test_a_token_opens_only_under_the_overrides_it_names(self) -> None:
        backend = stacked()
        overrides = (("binding", "none"),)
        named = backend.issue(request_for(), claims(), tokens=overrides)
        assert backend.open(request_for(), named, tokens=overrides).component
        assert _reason(lambda: backend.open(request_for(), named)) is (
            TokenRejection.URL_MISMATCH
        )
        plain = backend.issue(request_for(), claims())
        assert _reason(
            lambda: backend.open(request_for(), plain, tokens=overrides)
        ) is (TokenRejection.URL_MISMATCH)

    @pytest.mark.parametrize("sealed", ["", "a b", "a=b"])
    def test_a_sealed_text_outside_the_alphabet_is_malformed(self, sealed) -> None:
        token = f"{VERSION}.1.0.{sealed}"
        assert _reason(lambda: stacked().open(request_for(), token)) is (
            TokenRejection.MALFORMED
        )

    def test_a_refusal_is_not_announced(self) -> None:
        with capture_signals(boundary_token_rejected) as recorder:
            _reason(lambda: stacked().open(request_for(), "x"))
        assert len(recorder) == 0


class TestSettle:
    """`settle` compares the claims to the server's target, then the expiry."""

    def test_matching_live_claims_settle(self) -> None:
        backend = stacked()
        opened = backend.open(request_for(), backend.issue(request_for(), claims()))
        assert backend.settle(request_for(), opened, target()) == opened

    def test_claims_of_another_component_are_refused_unannounced(self) -> None:
        backend = stacked()
        opened = backend.open(request_for(), backend.issue(request_for(), claims()))
        with capture_signals(boundary_token_rejected) as recorder:
            reason = _reason(
                lambda: backend.settle(request_for(), opened, target(component="x"))
            )
        assert reason is TokenRejection.URL_MISMATCH
        assert len(recorder) == 0

    def test_claims_past_their_lifetime_are_expired(self) -> None:
        backend = stacked(MAX_AGE=60)
        with clocked(FrozenClock(NOW)):
            opened = backend.open(request_for(), backend.issue(request_for(), claims()))
        aged = replace(opened, issued=NOW)
        with clocked(FrozenClock(NOW + 60)):
            reason = _reason(lambda: backend.settle(request_for(), aged, target()))
        assert reason is TokenRejection.EXPIRED

    def test_a_policy_longer_at_settle_never_outlives_the_sealed_expiry(self) -> None:
        backend = stacked(MAX_AGE=f"{__name__}.age_of")
        _AGES.append(60)
        with clocked(FrozenClock(NOW)):
            opened = backend.open(request_for(), backend.issue(request_for(), claims()))
        assert opened.expires == NOW + 60
        _AGES.append(86_400)
        with clocked(FrozenClock(NOW + 60)):
            reason = _reason(lambda: backend.settle(request_for(), opened, target()))
        assert reason is TokenRejection.EXPIRED


class TestRedeem:
    """`redeem` is `open` then `settle`, so both paths refuse the same tokens."""

    def test_it_opens_under_the_target_overrides_then_settles(
        self, monkeypatch
    ) -> None:
        backend = stacked()
        overrides = (("max_age", 60),)
        token = backend.issue(request_for(), claims(), tokens=overrides)
        calls: list[str] = []
        opening, settling = backend.open, backend.settle

        def open_(request, token, *, tokens=()):
            calls.append(f"open {tokens}")
            return opening(request, token, tokens=tokens)

        def settle(request, claims, target):
            calls.append("settle")
            return settling(request, claims, target)

        monkeypatch.setattr(backend, "open", open_)
        monkeypatch.setattr(backend, "settle", settle)
        with clocked(FrozenClock(NOW)):
            backend.redeem(request_for(), token, target(tokens=overrides))
        assert calls == [f"open {overrides}", "settle"]

    @pytest.mark.parametrize(
        ("token", "changes", "reason"),
        [
            ("x", {}, TokenRejection.MALFORMED),
            (None, {"component": "other"}, TokenRejection.URL_MISMATCH),
        ],
    )
    def test_each_refusal_is_announced_once(self, token, changes, reason) -> None:
        backend = stacked()
        token = token or backend.issue(request_for(), claims())
        with capture_signals(boundary_token_rejected) as recorder:
            assert (
                _reason(lambda: backend.redeem(request_for(), token, target(**changes)))
                is reason
            )
        assert [event.kwargs["reason"] for event in recorder] == [reason]


class TestAnnounce:
    def test_a_refusal_open_raised_is_announced_with_its_component(self) -> None:
        error = BoundaryTokenError(TokenRejection.SIGNATURE)
        with capture_signals(boundary_token_rejected) as recorder:
            stacked().announce(request_for(), error, "card")
        (event,) = recorder
        assert event.kwargs["reason"] is TokenRejection.SIGNATURE
        assert event.kwargs["component"] == "card"
