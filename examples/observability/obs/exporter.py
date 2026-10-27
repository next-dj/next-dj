import hmac

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest
from prometheus_client import Counter, Gauge, Histogram

from next.realtime.frames import UpstreamError
from next.realtime.signals import UnsubscribeReason


TRANSPORTS = ("sse", "ws")

TOPIC_FAMILIES = frozenset({"obs"})
OTHER_FAMILY = "other"

DURATION_BUCKETS = (1, 5, 15, 60, 300, 900, 1800, 3600, 14400)

STREAMS_OPEN = Gauge(
    "next_realtime_streams_open",
    "Topic streams that hold a broker subscription.",
    ["transport"],
)
STREAM_UNSUBSCRIBES = Counter(
    "next_realtime_stream_unsubscribes",
    "Topic streams that left their topics.",
    ["transport", "reason"],
)
STREAM_DURATION = Histogram(
    "next_realtime_stream_duration_seconds",
    "Seconds a topic stream stayed subscribed.",
    ["transport"],
    buckets=DURATION_BUCKETS,
)
MESSAGES_PUBLISHED = Counter(
    "next_realtime_messages_published",
    "Numbered messages the broker accepted, by topic family.",
    ["family"],
)
PRESENCE_SNAPSHOTS = Counter(
    "next_realtime_presence_snapshots",
    "Presence snapshots the broker fanned out, by topic family.",
    ["family"],
)
UPSTREAM_EVENTS = Counter(
    "next_realtime_upstream_events",
    "Upstream events that reached a handler, by event name.",
    ["name"],
)
UPSTREAM_REJECTED = Counter(
    "next_realtime_upstream_rejected",
    "Upstream events that were not delivered, by the reason of the failed ack.",
    ["reason"],
)


def seed_series() -> None:
    """Create every bounded label child, so each series exports a zero from the start.

    Without the seed `rate()` over the unsubscribe counter has no series to read.
    """
    for transport in TRANSPORTS:
        STREAMS_OPEN.labels(transport=transport)
        STREAM_DURATION.labels(transport=transport)
        for reason in UnsubscribeReason:
            STREAM_UNSUBSCRIBES.labels(transport=transport, reason=reason.value)
    for family in (*TOPIC_FAMILIES, OTHER_FAMILY):
        MESSAGES_PUBLISHED.labels(family=family)
        PRESENCE_SNAPSHOTS.labels(family=family)
    for error in UpstreamError:
        UPSTREAM_REJECTED.labels(reason=error.value)


def topic_family(topic: str) -> str:
    """Return the label for `topic`, its prefix when listed, `other` otherwise.

    A topic per object would otherwise become one time series per object.
    """
    prefix = topic.partition(":")[0]
    return prefix if prefix in TOPIC_FAMILIES else OTHER_FAMILY


def totals_by(
    metric: Counter | Gauge | Histogram, label: str, suffix: str = ""
) -> dict[str, float]:
    """Sum the samples of `metric` named with `suffix` per value of `label`."""
    totals: dict[str, float] = {}
    for family in metric.collect():
        for sample in family.samples:
            if sample.name == f"{family.name}{suffix}":
                key = sample.labels[label]
                totals[key] = totals.get(key, 0) + sample.value
    return totals


def check_scrape(request: HttpRequest) -> None:
    """Refuse a scrape the bearer token does not admit.

    Without a token the endpoint answers only under `DEBUG` and is a 404 otherwise.
    """
    token = settings.OBS_METRICS_TOKEN
    if not token:
        if not settings.DEBUG:
            raise Http404
        return
    scheme, _, presented = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        presented.encode(), token.encode()
    ):
        raise PermissionDenied
