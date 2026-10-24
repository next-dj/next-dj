import pytest

from next.realtime.manager import broker_manager
from next.testing.realtime import SocketTicket
from tests.support.realtime import issue_session_ticket


@pytest.fixture(autouse=True)
def _reset_broker_manager():
    broker_manager.reset()
    yield
    broker_manager.reset()


@pytest.fixture()
def session_ticket(transactional_db: None) -> SocketTicket:
    """Issue the default ``room:42`` ticket against a fresh saved session."""
    return issue_session_ticket()
