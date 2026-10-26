from next.partial import Patches
from next.realtime import Upstream, apublish, on


PULSE_TOPIC = "obs:pulse"
REALTIME_TOTALS_ZONE = "realtime-totals"


def can_watch(_user: object, topic: str) -> bool:
    """Grant the pulse topic to every visitor and nothing else."""
    return topic == PULSE_TOPIC


@on(PULSE_TOPIC, "wave")
async def wave(upstream: Upstream) -> None:
    """Ask every open realtime page to re-read its totals after a wave."""
    await apublish(upstream.topic, Patches(None).refresh(zone=REALTIME_TOTALS_ZONE))
