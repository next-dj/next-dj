from next.suspense.tokens import FrozenClock, TokenClock, clocked, token_clock


class TestClock:
    """Tokens read a replaceable clock, so a test needs no patched ``time``."""

    def test_the_system_clock_reads_whole_seconds(self) -> None:
        assert isinstance(TokenClock().now(), int)
        assert isinstance(token_clock(), TokenClock)

    def test_a_frozen_clock_holds_and_moves(self) -> None:
        with clocked(FrozenClock(10)) as clock:
            assert token_clock() is clock
            clock.advance(5)
            assert token_clock().now() == 15
        assert token_clock() is not clock
