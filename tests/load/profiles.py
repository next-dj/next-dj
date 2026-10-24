import math
from collections.abc import Mapping
from dataclasses import dataclass, replace


TOPIC_PREFIX = "load:"


@dataclass(frozen=True, slots=True)
class Profile:
    """One load shape with the loss and error allowance it must stay within."""

    name: str
    publishers: int
    sse_clients: int
    ws_clients: int
    topics: int
    rate: float
    duration: float
    client_processes: int
    ready_timeout: float
    connect_concurrency: int
    max_lost_ratio: float
    max_client_lost_ratio: float
    max_error_ratio: float
    max_resync_ratio: float

    @property
    def messages(self) -> int:
        """Return how many messages one publisher schedules, one per rate tick."""
        return math.ceil(self.rate * self.duration)

    @property
    def scheduled(self) -> int:
        """Return how many messages all publishers schedule together."""
        return self.publishers * self.messages

    @property
    def clients(self) -> Mapping[str, int]:
        """Return how many clients each transport must end with."""
        return {"sse": self.sse_clients, "ws": self.ws_clients}

    @property
    def topic_names(self) -> tuple[str, ...]:
        """Return the stream route names, the topic without its `load:` prefix."""
        return tuple(f"t{index}" for index in range(self.topics))


PROFILES: Mapping[str, Profile] = {
    "small": Profile(
        name="small",
        publishers=2,
        sse_clients=50,
        ws_clients=50,
        topics=5,
        rate=20.0,
        duration=20.0,
        client_processes=1,
        ready_timeout=30.0,
        connect_concurrency=64,
        max_lost_ratio=0.0,
        max_client_lost_ratio=0.0,
        max_error_ratio=0.0,
        max_resync_ratio=0.0,
    ),
    "medium": Profile(
        name="medium",
        publishers=4,
        sse_clients=500,
        ws_clients=500,
        topics=20,
        rate=50.0,
        duration=30.0,
        client_processes=4,
        ready_timeout=120.0,
        connect_concurrency=64,
        max_lost_ratio=0.001,
        max_client_lost_ratio=0.01,
        max_error_ratio=0.01,
        max_resync_ratio=0.01,
    ),
}


def resolve(environ: Mapping[str, str]) -> Profile:
    """Return the profile named by `LOAD_PROFILE`, its duration overridable for smokes.

    An unknown name raises `LookupError` and a bad `LOAD_DURATION` raises `ValueError`.
    """
    name = environ.get("LOAD_PROFILE", "small") or "small"
    profile = PROFILES.get(name)
    if profile is None:
        msg = f"Unknown LOAD_PROFILE {name!r}, expected one of {sorted(PROFILES)}."
        raise LookupError(msg)
    raw = environ.get("LOAD_DURATION")
    if not raw:
        return profile
    duration = float(raw)
    if not math.isfinite(duration) or duration <= 0:
        msg = f"LOAD_DURATION must be a positive number of seconds, got {raw!r}."
        raise ValueError(msg)
    return replace(profile, duration=duration)
