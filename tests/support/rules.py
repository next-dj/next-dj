from django.http import HttpRequest

from next.transactions import ViewKind


KINDS_SEEN: list[ViewKind] = []


def record_kind(request: HttpRequest, kind: ViewKind) -> tuple[str, ...]:
    """Note the view kind the transaction rule is asked about and open nothing."""
    KINDS_SEEN.append(kind)
    return ()
