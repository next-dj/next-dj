from polls.models import Poll


def can_subscribe_topic(_user: object, topic: str) -> bool:
    """Allow a public poll topic only while that poll still exists."""
    prefix, separator, raw_pk = topic.partition(":")
    if prefix != "poll" or not separator or not raw_pk.isdecimal():
        return False
    return Poll.objects.filter(pk=int(raw_pk)).exists()
