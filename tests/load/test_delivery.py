import sys
from pathlib import Path

import pytest

from tests.load import harness, report
from tests.load.profiles import Profile


RESULTS = Path(__file__).parent / "results"


@pytest.mark.load()
@pytest.mark.usefixtures("redis_prefix")
def test_realtime_delivery_under_load(profile: Profile, stack: dict[str, object]):
    try:
        outcome = harness.run(profile)
    except harness.HarnessError as exc:
        pytest.fail(str(exc))

    result = report.document(profile, stack, outcome)
    path = report.write(result, RESULTS, profile.name)
    sys.stdout.write(report.summary(profile, result, path))

    assert outcome.published + outcome.publish_errors == profile.scheduled, (
        f"published {outcome.published} plus {outcome.publish_errors} errors, "
        f"scheduled {profile.scheduled}"
    )
    assert outcome.publish_errors == 0, (
        f"publishers hit {outcome.publish_errors} broker errors"
    )
    for name, count in profile.clients.items():
        if not count:
            continue
        clients = outcome.transports[name].clients
        assert clients == count, f"{name}: {clients} clients, profile has {count}"
    for name, totals in outcome.transports.items():
        if not profile.clients[name]:
            continue
        errors = sum(totals.errors.values())
        allowed_lost = profile.max_lost_ratio * totals.expected
        assert totals.hello == totals.clients, f"{name}: not every client got hello"
        assert totals.delivered > 0, f"{name}: nothing was delivered"
        assert totals.skipped == 0, f"{name}: {totals.skipped} skipped"
        assert totals.duplicates == 0, f"{name}: {totals.duplicates} duplicates"
        assert totals.excess == 0, f"{name}: {totals.excess} beyond the published total"
        assert totals.lost <= allowed_lost, (
            f"{name}: lost {totals.lost} of {totals.expected}"
        )
        assert totals.worst_lost_ratio <= profile.max_client_lost_ratio, (
            f"{name}: one client lost {totals.worst_lost_ratio:.4f} of its messages"
        )
        assert totals.resyncs <= profile.max_resync_ratio * totals.clients, (
            f"{name}: {totals.resyncs} resyncs across {totals.clients} clients"
        )
        assert totals.restarts == 0, f"{name}: {totals.restarts} epoch restarts"
        assert totals.gave_up == 0, f"{name}: {totals.gave_up} clients gave up"
        assert totals.gaps <= allowed_lost, (
            f"{name}: {totals.gaps} gaps in the sequence of {totals.expected}"
        )
        assert errors <= profile.max_error_ratio * totals.clients, (
            f"{name}: {errors} errors {dict(totals.errors)}"
        )
