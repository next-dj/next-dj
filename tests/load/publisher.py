import time
from collections import Counter
from dataclasses import dataclass, field
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event

import django

from next.partial import Patches
from next.realtime import BrokerConnectionError, publish
from next.realtime.manager import broker_manager
from tests.load.boot import boot
from tests.load.frames import LOAD_EVENT
from tests.load.profiles import TOPIC_PREFIX
from tests.load.ready import Ready


@dataclass(frozen=True, slots=True)
class PublisherJob:
    """What one publisher process sends, how fast, and for how long."""

    index: int
    topics: tuple[str, ...]
    rate: float
    messages: int
    start_timeout: float


@dataclass(slots=True)
class PublisherReport:
    """Per-topic counts one publisher had accepted by the broker."""

    published: Counter[str] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)
    elapsed: float = 0.0


def run_publisher(
    job: PublisherJob,
    ready: Queue[Ready],
    start: Event,
    abort: Event,
    reports: Queue[PublisherReport],
) -> None:
    """Configure Django, warm the broker, report ready and publish after `start`.

    The warm-up opens the Redis connection so the first message does not time it.
    """
    boot()
    django.setup()
    topics = [f"{TOPIC_PREFIX}{name}" for name in job.topics]
    broker_manager.get().positions(topics)
    report = PublisherReport()
    ready.put(Ready("publisher"))
    if start.wait(job.start_timeout) and not abort.is_set():
        _publish_for(job, abort, report)
    reports.put(report)


def _publish_for(job: PublisherJob, abort: Event, report: PublisherReport) -> None:
    """Publish round-robin across the topics, each message at its tick of the rate."""
    begin = time.monotonic()
    for sent in range(job.messages):
        delay = begin + sent / job.rate - time.monotonic()
        if (delay > 0 and abort.wait(delay)) or abort.is_set():
            break
        topic = f"{TOPIC_PREFIX}{job.topics[(sent + job.index) % len(job.topics)]}"
        patches = Patches(None).event(
            LOAD_EVENT, {"sent": time.time(), "publisher": job.index}
        )
        try:
            publish(topic, patches, on_commit=False)
        except BrokerConnectionError as exc:
            report.errors[type(exc).__name__] += 1
        else:
            report.published[topic] += 1
    report.elapsed = time.monotonic() - begin
