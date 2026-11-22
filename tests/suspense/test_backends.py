import pytest

from next.suspense import Admission, Admit, Capacity, QueueDelay
from next.testing import override_next_settings


def _capacity(queued: int, delay: float = 0.0) -> Capacity:
    return Capacity(workers=16, busy=16, queued=queued, delay=delay)


class TestQueueDelay:
    """``QueueDelay`` queues or hands over from its inputs alone, and never sheds."""

    def test_it_reads_the_executor_options(self) -> None:
        admission = QueueDelay()
        assert isinstance(admission, Admission)
        assert (admission.timeout, admission.limit) == (0.05, 64)

    @pytest.mark.parametrize(
        ("queued", "delay", "forced", "admitted"),
        [
            (0, 0.0, False, Admit.POOL),
            (63, 0.05, False, Admit.POOL),
            (1, 0.06, False, Admit.REQUEST),
            (64, 0.06, False, Admit.REQUEST),
            (64, 0.0, False, Admit.REQUEST),
            (0, 0.0, True, Admit.REQUEST),
            (64, 0.0, True, Admit.REQUEST),
        ],
    )
    def test_the_admission_table(
        self, queued: int, delay: float, forced, admitted: Admit
    ) -> None:
        verdict = QueueDelay().admit(_capacity(queued, delay), forced=forced)
        assert verdict is admitted

    def test_the_settings_move_the_thresholds(self) -> None:
        options = {"QUEUE_TIMEOUT": "10ms", "MAX_QUEUE": 2}
        with override_next_settings(DEFER={"EXECUTOR": {"OPTIONS": options}}):
            admission = QueueDelay()
        assert admission.admit(_capacity(1, 0.02), forced=False) is Admit.REQUEST
        assert admission.admit(_capacity(2), forced=False) is Admit.REQUEST
        assert admission.admit(_capacity(1), forced=False) is Admit.POOL
