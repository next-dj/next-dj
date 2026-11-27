from collections.abc import Callable

import pytest

from next.defer.components import current_deferring
from next.defer.markers import Job
from next.defer.response import shell
from tests.support.boundaries import make_call, make_job


@pytest.fixture()
def queued() -> Callable[..., tuple[str, Job]]:
    """Return a function that queues one job into the open block with its wrapper."""

    def queue(html: str = "R") -> tuple[str, Job]:
        held = current_deferring()
        assert held is not None
        session = held.open()
        job = make_job(session, html)
        session.queue(job)
        return shell(job.id, make_call(), "L"), job

    return queue
