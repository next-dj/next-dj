import pytest
from django.db import connection, transaction

from next.defer.coordinator import Coordinator
from next.defer.markers import Rendered
from tests.defer.connections.shared import query
from tests.defer.coordinator.drivers.doubles import new_session, queue_job


def _querying() -> Rendered:
    query("SELECT 1")
    return Rendered("done", "")


@pytest.mark.django_db(transaction=True)
class TestRequestLaneDeadline:
    """A request-lane job hands its deadline to PostgreSQL like a pool job does."""

    @pytest.fixture()
    def calls(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, ...]]:
        calls: list[tuple[object, ...]] = []
        connection.ensure_connection()
        connection.connection.create_function(
            "set_config", 3, lambda *args: calls.append(args) or args[1]
        )
        monkeypatch.setattr(connection, "vendor", "postgresql")
        return calls

    def _run(self) -> None:
        session = new_session(deadline=60.0)
        queue_job(session, _querying, parallel=False)
        list(Coordinator().run(session))

    def test_a_request_lane_query_runs_under_the_deadline(
        self, calls: list[tuple[object, ...]]
    ) -> None:
        self._run()
        assert [name for name, _, _ in calls] == [
            "statement_timeout",
            "idle_in_transaction_session_timeout",
        ]
        assert not connection.in_atomic_block

    def test_the_open_transaction_of_the_request_stays_untouched(
        self, calls: list[tuple[object, ...]]
    ) -> None:
        with transaction.atomic():
            self._run()
            assert connection.in_atomic_block
        assert calls == []
