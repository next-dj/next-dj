from obs.exporter import (
    MESSAGES_PUBLISHED,
    STREAM_DURATION,
    STREAM_UNSUBSCRIBES,
    STREAMS_OPEN,
    UPSTREAM_EVENTS,
    totals_by,
)
from obs.realtime import PULSE_TOPIC, REALTIME_TOTALS_ZONE

from next import context
from next.pages import MetadataDict


metadata: MetadataDict = {"title": "Realtime"}


@context("pulse_topic")
def pulse_topic() -> str:
    """Name the topic the wave button sends its upstream event on."""
    return PULSE_TOPIC


@context("realtime", zone=REALTIME_TOTALS_ZONE)
def realtime() -> dict[str, object]:
    """Read the stream series back from the process registry the exporter serves."""
    open_streams = totals_by(STREAMS_OPEN, "transport")
    closed = sum(totals_by(STREAM_DURATION, "transport", "_count").values())
    seconds = sum(totals_by(STREAM_DURATION, "transport", "_sum").values())
    reasons = totals_by(STREAM_UNSUBSCRIBES, "reason", "_total")
    return {
        "open_sse": int(open_streams.get("sse", 0)),
        "open_ws": int(open_streams.get("ws", 0)),
        "published": int(
            sum(totals_by(MESSAGES_PUBLISHED, "family", "_total").values())
        ),
        "waves": int(totals_by(UPSTREAM_EVENTS, "name", "_total").get("wave", 0)),
        "mean_seconds": round(seconds / closed, 1) if closed else 0,
        "reasons": sorted(
            ((reason, int(count)) for reason, count in reasons.items()),
            key=lambda item: (-item[1], item[0]),
        ),
    }
