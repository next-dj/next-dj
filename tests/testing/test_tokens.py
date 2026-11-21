import pytest

from next.suspense import BoundaryTokenError, TokenRejection
from next.suspense.manager import suspense_manager
from next.suspense.tokens import BoundaryClaims, token_clock
from next.testing import (
    FrozenClock,
    expire_boundary_token,
    freeze_token_clock,
    issue_boundary_token,
    override_next_settings,
)
from tests.support.tokens import NOW, claims, request_for, target


class _Plain:
    """A token backend other than the shipped stack."""

    def __init__(self) -> None:
        self.issued: list[int] = []

    def issue(self, request, claims, *, tokens=()) -> str:
        self.issued.append(token_clock().now())
        return "plain"


class TestFreezeTokenClock:
    def test_the_clock_is_held_inside_the_block(self) -> None:
        with freeze_token_clock(NOW) as clock:
            assert isinstance(clock, FrozenClock)
            assert token_clock().now() == NOW
        assert token_clock() is not clock


class TestIssueBoundaryToken:
    """``issue_boundary_token`` mints through the configured backend."""

    def test_a_token_minted_now_redeems(self) -> None:
        token = issue_boundary_token(request_for(), claims(sku="A1"))
        redeemed = suspense_manager.tokens().redeem(request_for(), token, target())
        assert redeemed.props == (("sku", "A1"),)

    def test_a_token_minted_at_an_instant_carries_it(self) -> None:
        token = issue_boundary_token(request_for(), claims(), at=NOW)
        with freeze_token_clock(NOW + 1):
            redeemed = suspense_manager.tokens().redeem(request_for(), token, target())
        assert redeemed.issued == NOW

    def test_component_overrides_reach_the_backend(self) -> None:
        tokens = (("max_age", 60),)
        token = issue_boundary_token(request_for(), claims(), tokens=tokens, at=NOW)
        with freeze_token_clock(NOW + 60), pytest.raises(BoundaryTokenError):
            suspense_manager.tokens().redeem(
                request_for(), token, target(tokens=tokens)
            )


class TestExpireBoundaryToken:
    """``expire_boundary_token`` mints a token whose lifetime already ended."""

    def test_the_token_answers_with_a_reload(self) -> None:
        token = expire_boundary_token(request_for(), claims())
        with pytest.raises(BoundaryTokenError) as caught:
            suspense_manager.tokens().redeem(request_for(), token, target())
        assert caught.value.reason is TokenRejection.EXPIRED

    def test_it_follows_the_component_overrides(self) -> None:
        tokens = (("max_age", 60),)
        with freeze_token_clock(NOW):
            token = expire_boundary_token(request_for(), claims(), tokens=tokens)
            backend = suspense_manager.tokens()
            for policy in ((), tokens):
                with pytest.raises(BoundaryTokenError) as caught:
                    backend.redeem(request_for(), token, target(tokens=policy))
                assert caught.value.reason is TokenRejection.EXPIRED

    def test_a_policy_shortens_the_sealed_lifetime_and_never_extends_it(self) -> None:
        short = (("max_age", 60),)
        backend = suspense_manager.tokens()
        brief = issue_boundary_token(request_for(), claims(), tokens=short, at=NOW)
        full = issue_boundary_token(request_for(), claims(), at=NOW)
        with freeze_token_clock(NOW + 59):
            assert backend.redeem(request_for(), brief, target())
            assert backend.redeem(request_for(), full, target(tokens=short))
        with freeze_token_clock(NOW + 60):
            assert backend.redeem(request_for(), full, target())
            for token, policy in ((brief, ()), (full, short)):
                with pytest.raises(BoundaryTokenError) as caught:
                    backend.redeem(request_for(), token, target(tokens=policy))
                assert caught.value.reason is TokenRejection.EXPIRED

    def test_another_backend_mints_at_the_epoch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plain = _Plain()
        monkeypatch.setattr(suspense_manager, "tokens", lambda: plain)
        claimed = BoundaryClaims.of("card", "scope")
        assert expire_boundary_token(request_for(), claimed) == "plain"
        assert plain.issued == [0]

    def test_a_reset_backend_follows_the_settings(self) -> None:
        with override_next_settings(DEFER={"TOKENS": {"OPTIONS": {"MAX_AGE": 5}}}):
            token = expire_boundary_token(request_for(), claims())
            with pytest.raises(BoundaryTokenError):
                suspense_manager.tokens().redeem(request_for(), token, target())
