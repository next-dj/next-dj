import time

from next.bridge.deadline import deadline, remaining


class TestRemaining:
    def test_no_deadline_answers_none(self) -> None:
        assert remaining() is None

    def test_a_deadline_answers_the_seconds_left(self) -> None:
        with deadline(time.monotonic() + 60):
            left = remaining()
        assert left is not None
        assert 0 < left <= 60
        assert remaining() is None

    def test_a_passed_deadline_answers_zero(self) -> None:
        with deadline(time.monotonic() - 1):
            assert remaining() == 0.0

    def test_a_nested_none_lifts_the_deadline_for_its_block(self) -> None:
        with deadline(time.monotonic() + 60):
            with deadline(None):
                assert remaining() is None
            assert remaining() is not None
