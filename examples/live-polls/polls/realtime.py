from django.utils import timezone

from next.partial import Patches
from next.realtime import publish
from polls.models import Poll


def can_subscribe_topic(_user: object, topic: str) -> bool:
    """Allow a public poll topic only while that poll still exists."""
    prefix, separator, raw_pk = topic.partition(":")
    if prefix != "poll" or not separator or not raw_pk.isdecimal():
        return False
    return Poll.objects.filter(pk=int(raw_pk)).exists()


def publish_phase(poll: Poll, *, on_commit: bool = True) -> None:
    """Tell open poll pages the current voting phase and deadline."""
    deadline = (
        int(poll.closes_at.timestamp() * 1000) if poll.closes_at is not None else None
    )
    payload = {
        "is_open": poll.is_open,
        "accepting_votes": poll.accepts_votes(at=timezone.now()),
        "deadline": deadline,
    }
    publish(
        f"poll:{poll.pk}",
        Patches(None).event("poll:phase", payload),
        on_commit=on_commit,
        using=poll._state.db or "default",
    )
