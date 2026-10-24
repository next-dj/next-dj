import pytest

from next.realtime.tickets import issue_ticket, verify_ticket
from tests.benchmarks.factories import StubRequest, topic_names
from tests.support.realtime import ROOM_AUTHORIZER_REF


class TestBenchTicket:
    """Sign and verify a session-bound WebSocket grant."""

    @pytest.mark.parametrize("count", [1, 64])
    @pytest.mark.benchmark(group="realtime.ticket")
    def test_issue(self, count: int, benchmark) -> None:
        request = StubRequest()
        topics = tuple(topic_names(count))

        def run() -> object:
            return issue_ticket(
                request, topics, transform_ref=None, authorizer_ref=ROOM_AUTHORIZER_REF
            )

        grant = benchmark(run)
        claims = verify_ticket(grant.token, request.session.session_key)
        assert claims.topics == topics

    @pytest.mark.parametrize("count", [1, 64])
    @pytest.mark.benchmark(group="realtime.ticket")
    def test_verify(self, count: int, benchmark) -> None:
        request = StubRequest()
        grant = issue_ticket(
            request,
            tuple(topic_names(count)),
            transform_ref=None,
            authorizer_ref=ROOM_AUTHORIZER_REF,
        )
        claims = benchmark(verify_ticket, grant.token, request.session.session_key)
        assert claims.topics == tuple(topic_names(count))
        assert claims.authorizer_ref == ROOM_AUTHORIZER_REF
