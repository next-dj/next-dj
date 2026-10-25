import threading
from multiprocessing.synchronize import Event

from next.realtime import RedisBroker


TOPIC = "room:1"
CONNECTION = "crashing"


def attend_until_killed(url: str, prefix: str, ttl: float, joined: Event) -> None:
    """Join from a spawned process that never configures Django and wait to be killed.

    The closed schedule leaves the snapshot to the surviving process.
    """
    backend = RedisBroker(
        {"OPTIONS": {"URL": url, "PREFIX": prefix}}, replay={}, presence={"WINDOW": 0.1}
    )
    backend.presence_schedule.close()
    backend.join(TOPIC, CONNECTION, None, ttl)
    joined.set()
    threading.Event().wait(120)
