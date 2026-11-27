from collections.abc import Callable, Iterator

import pytest
from django.test import override_settings

from next.defer.coordinator.drivers.base import serial_notice
from next.defer.markers import LaneFacts, ServerKind
from tests.defer.coordinator.drivers.doubles import (
    CountingPool,
    coordinator_over,
    new_session,
    queue_job,
    ready_body,
)


_SQLITE = LaneFacts(ServerKind.WSGI_THREADED, sqlite=("default",))
_NOTE = "SQLITE"


@pytest.fixture(autouse=True)
def fresh_notice() -> Iterator[None]:
    serial_notice.forget()
    yield
    serial_notice.forget()


type _Pools = Callable[[int], CountingPool]


def _run(pools: _Pools, facts: LaneFacts, jobs: int = 2) -> None:
    session = new_session(facts=facts)
    for index in range(jobs):
        queue_job(session, ready_body(str(index)))
    list(coordinator_over(pools(2)).run(session))


def _notes(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "next.defer" and _NOTE in record.getMessage()
    ]


class TestSerialNotice:
    """Under DEBUG the first page SQLite keeps serial says why, once per process."""

    @override_settings(DEBUG=True)
    def test_two_boundaries_kept_serial_warn_once(
        self, pools: _Pools, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING", logger="next.defer"):
            _run(pools, _SQLITE)
            _run(pools, _SQLITE)
        [note] = _notes(caplog)
        assert "EXECUTOR']['SQLITE'] = \"parallel\"" in note
        assert "write" in note

    @pytest.mark.parametrize(
        ("facts", "jobs"),
        [
            pytest.param(_SQLITE, 1, id="one-boundary"),
            pytest.param(
                LaneFacts(
                    ServerKind.WSGI_THREADED, sqlite=("default",), memory=("default",)
                ),
                2,
                id="in-memory",
            ),
            pytest.param(
                LaneFacts(
                    ServerKind.WSGI_THREADED, sqlite=("default",), sqlite_parallel=True
                ),
                2,
                id="parallel",
            ),
            pytest.param(
                LaneFacts(
                    ServerKind.WSGI_THREADED, sqlite=("default",), parallel=False
                ),
                2,
                id="opted-out",
            ),
            pytest.param(
                LaneFacts(
                    ServerKind.WSGI_THREADED, sqlite=("default",), atomic=("default",)
                ),
                2,
                id="atomic",
            ),
        ],
    )
    @override_settings(DEBUG=True)
    def test_nothing_the_setting_cures_says_anything(
        self,
        pools: _Pools,
        caplog: pytest.LogCaptureFixture,
        facts: LaneFacts,
        jobs: int,
    ) -> None:
        with caplog.at_level("WARNING", logger="next.defer"):
            _run(pools, facts, jobs)
        assert _notes(caplog) == []

    def test_production_says_nothing(
        self, pools: _Pools, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING", logger="next.defer"):
            _run(pools, _SQLITE)
        assert _notes(caplog) == []
