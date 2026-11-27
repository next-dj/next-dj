from django.http import HttpRequest

from next.requests import RequestKind


KINDS_SEEN: list[RequestKind] = []


def record_kind(request: HttpRequest, kind: RequestKind) -> tuple[str, ...]:
    """Note the view kind the transaction rule is asked about and open nothing."""
    KINDS_SEEN.append(kind)
    return ()
