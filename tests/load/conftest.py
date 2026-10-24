import os
import uuid
from collections.abc import Iterator

import pytest
import redis

from tests.load import harness, machine
from tests.load.profiles import Profile, resolve


@pytest.fixture()
def redis_url() -> str:
    """Return the Redis the run publishes through, failing when none is given."""
    url = os.environ.get("REDIS_URL", "")
    if not url:
        pytest.fail("REDIS_URL is not set, run the harness through `make load`.")
    return url


@pytest.fixture()
def profile() -> Profile:
    """Return the profile named by the environment."""
    try:
        return resolve(os.environ)
    except (LookupError, ValueError) as exc:
        pytest.fail(str(exc))


@pytest.fixture()
def stack(redis_url: str) -> dict[str, object]:
    """Describe the machine and software stack, failing when Redis does not answer."""
    try:
        return machine.describe(redis_url)
    except redis.RedisError as exc:
        pytest.fail(f"Redis at {redis_url} did not answer: {exc}")


@pytest.fixture()
def redis_prefix(redis_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Key the run under a unique prefix that spawned processes inherit, then purge it."""
    prefix = f"nextdj-load-{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("LOAD_REDIS_PREFIX", prefix)
    yield prefix
    harness.purge(redis_url, prefix)
