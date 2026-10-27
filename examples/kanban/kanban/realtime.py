from collections.abc import Mapping

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from kanban.models import Board, Card
from next.partial import Patches
from next.realtime import Upstream, on, publish


TOPIC_PREFIX = "board"
GUEST = {"name": "A guest", "initials": "?"}
GUEST_TAG_LENGTH = 4
MEMBER_KEY_LENGTH = 12


def board_topic(board_pk: int) -> str:
    """Return the topic every open tab of one board subscribes to."""
    return f"{TOPIC_PREFIX}:{board_pk}"


def board_pk(topic: str) -> int | None:
    """Return the board pk a board topic names, None for any other topic."""
    prefix, separator, raw_pk = topic.partition(":")
    if prefix != TOPIC_PREFIX or not separator or not raw_pk.isdecimal():
        return None
    return int(raw_pk)


def can_watch_board(user: AbstractBaseUser | AnonymousUser, topic: str) -> bool:
    """Allow a board topic while the board exists and the visitor may see it."""
    pk = board_pk(topic)
    if pk is None:
        return False
    return Board.objects.visible_to(user).filter(pk=pk).exists()


def public_profile(user: AbstractBaseUser) -> dict[str, str]:
    """Describe a signed-in member by display name and initials."""
    name = user.get_full_name() or user.get_username()
    initials = "".join(part[0] for part in name.split()[:2]).upper()
    return {"name": name, "initials": initials}


def identify_member(
    user: AbstractBaseUser | AnonymousUser, _topic: str, subject: str
) -> Mapping[str, str] | None:
    """Name a member by profile or guest tag, keyed by its opaque subject.

    The key keeps two members with one display name apart without exposing a pk.
    """
    key = subject[:MEMBER_KEY_LENGTH]
    if user.is_authenticated:
        return {**public_profile(user), "key": key}
    if not subject:
        return None
    return {"name": f"Guest {subject[:GUEST_TAG_LENGTH]}", "initials": "G", "key": key}


@on(f"{TOPIC_PREFIX}:{{id}}", "dragging")
def dragging(upstream: Upstream) -> None:
    """Tell the board who picked up or dropped a card, and from which socket."""
    data = upstream.data if isinstance(upstream.data, dict) else {}
    card = data.get("card")
    active = data.get("active")
    if type(card) is not int or type(active) is not bool:
        return
    cards = Card.objects.filter(column__board_id=upstream.params["id"])
    if not cards.filter(pk=card).exists():
        return
    by = identify_member(upstream.user, upstream.topic, upstream.subject) or GUEST
    detail = {"card": card, "active": active, "by": by, "from": upstream.connection}
    publish(
        upstream.topic, Patches(None).event("board:dragging", detail), on_commit=False
    )
