import pytest

from next.defer import Admission, Admit, Capacity, QueueDelay
from next.testing import override_next_settings


def _capacity(queued: int, delay: float = 0.0) -> Capacity:
    return Capacity(workers=16, busy=16, queued=queued, delay=delay)


class TestQueueDelay:
    """``QueueDelay`` queues, hands over or sheds from the pool reading alone."""

    def test_it_reads_the_executor_options(self) -> None:
        admission = QueueDelay()
        assert isinstance(admission, Admission)
        assert (admission.timeout, admission.shed, admission.limit) == (0.05, 1.0, 64)

    @pytest.mark.parametrize(
        ("queued", "delay", "admitted"),
        [
            (0, 0.0, Admit.POOL),
            (63, 0.05, Admit.POOL),
            (1, 0.06, Admit.REQUEST),
            (64, 0.06, Admit.REQUEST),
            (64, 0.0, Admit.REQUEST),
            (1, 0.99, Admit.REQUEST),
            (1, 1.0, Admit.SHED),
            (64, 2.0, Admit.SHED),
        ],
    )
    def test_the_admission_table(
        self, queued: int, delay: float, admitted: Admit
    ) -> None:
        assert QueueDelay().admit(_capacity(queued, delay)) is admitted

    def test_the_settings_move_the_thresholds(self) -> None:
        executor = {
            "QUEUE_TIMEOUT": "10ms",
            "SHED_DELAY": "50ms",
            "OPTIONS": {"MAX_QUEUE": 2},
        }
        with override_next_settings(DEFER={"EXECUTOR": executor}):
            admission = QueueDelay()
        assert admission.admit(_capacity(1, 0.02)) is Admit.REQUEST
        assert admission.admit(_capacity(2)) is Admit.REQUEST
        assert admission.admit(_capacity(1)) is Admit.POOL
        assert admission.admit(_capacity(0, 0.05)) is Admit.SHED
