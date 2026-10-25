from multiprocessing.synchronize import Event

from next.partial import Envelope
from next.realtime import RedisBroker


WORKER_BATCH = 25
TOPIC = "poll:1"


def publish_batch(url: str, prefix: str, envelope: Envelope, start: Event) -> None:
    """Publish one batch from a spawned process that never configures Django.

    Passing the window explicitly keeps the broker from reading Django settings.
    """
    backend = RedisBroker({"OPTIONS": {"URL": url, "PREFIX": prefix}}, replay={})
    start.wait(30)
    for _ in range(WORKER_BATCH):
        backend.publish(TOPIC, envelope)
    backend.close()
