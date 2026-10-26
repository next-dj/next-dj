"""The WebSocket ticket a topic stream URL answers once the client asks for a socket."""

from http import HTTPStatus

from django.http import HttpRequest, JsonResponse
from django.utils.cache import patch_vary_headers
from django.utils.module_loading import import_string

from next.forms.transport import TRANSPORT_HEADER

from .binding import ticket_binding
from .errors import InvalidTicketError, InvalidTopicError
from .messages import MAX_TOPICS, validate_topics
from .tickets import MAX_REFERENCE_LENGTH, issue_ticket
from .transports import active_transports, warn_unmounted_websocket


type Callback = tuple[object, object]
"""A callback paired with the dotted path it was given as, None for a callable."""

type _Grant = tuple[str | None, str, bool | str]

_COMPACT = {"separators": (",", ":")}


def wants_websocket(request: HttpRequest) -> bool:
    """Whether the client asked to negotiate the WebSocket transport."""
    headers = getattr(request, "headers", {})
    return str(headers.get(TRANSPORT_HEADER, "")).lower() == "ws"


def negotiate(
    request: HttpRequest,
    topics: tuple[str, ...],
    *,
    transform: Callback,
    authorize: Callback,
    presence: Callback,
) -> JsonResponse:
    """Answer with a session-bound ticket, or with the reason the client stays on SSE.

    Each callback must import from a stable dotted path, since the ticket names it.
    """
    status, body, headers = _reply(
        request, topics, _ticket_references(transform, authorize, presence)
    )
    response = JsonResponse(body, status=status, json_dumps_params=_COMPACT)
    for header, value in headers.items():
        response[header] = value
    if status == HTTPStatus.OK:
        patch_vary_headers(response, ["Cookie", TRANSPORT_HEADER])
        ticket_binding().respond(request, response)
    return response


def _reply(
    request: HttpRequest, topics: tuple[str, ...], refs: _Grant | str
) -> tuple[int, dict[str, object], dict[str, str]]:
    """Return the status, JSON body and headers of a ticket or SSE fallback reply."""
    if getattr(request, "method", "GET") != "GET":
        return HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method"}, {"Allow": "GET"}
    if "ws" not in active_transports():
        return HTTPStatus.NOT_ACCEPTABLE, {"error": "transport_unavailable"}, {}
    warn_unmounted_websocket()
    selected = _grantable_topics(topics)
    if selected is None:
        return HTTPStatus.BAD_REQUEST, {"error": "invalid_topics"}, {}
    if isinstance(refs, str):
        return HTTPStatus.NOT_ACCEPTABLE, {"transport": "sse", "reason": refs}, {}
    transform_ref, authorizer_ref, presence = refs
    try:
        grant = issue_ticket(
            request,
            selected,
            transform_ref=transform_ref,
            authorizer_ref=authorizer_ref,
            presence=presence,
        )
    except InvalidTicketError:
        return HTTPStatus.SERVICE_UNAVAILABLE, {"error": "session_unavailable"}, {}
    return HTTPStatus.OK, grant.as_dict(), {"Cache-Control": "private, no-store"}


def _grantable_topics(topics: tuple[str, ...]) -> tuple[str, ...] | None:
    """Return the unique valid topics a ticket can grant, or None."""
    try:
        selected = validate_topics(topics)
    except InvalidTopicError:
        return None
    if not selected or len(selected) > MAX_TOPICS:
        return None
    return selected


def _ticket_references(
    transform: Callback, authorizer: Callback, presence: Callback
) -> _Grant | str:
    """Require import-stable transform, authorizer and identify, or name the reason."""
    resolved_ref = _reference(*transform)
    if transform[0] is not None and resolved_ref is None:
        return "transform"
    resolved_authorizer = _reference(*authorizer)
    if resolved_authorizer is None:
        return "authorization"
    mode, identify_ref = presence
    if isinstance(mode, bool):
        return resolved_ref, resolved_authorizer, mode
    resolved_identify = _reference(mode, identify_ref)
    if resolved_identify is None:
        return "presence"
    return resolved_ref, resolved_authorizer, resolved_identify


def _reference(callback: object, explicit: object) -> str | None:
    """Return the import-stable dotted path of a callback a ticket can name, or None."""
    if callback is None:
        return None
    candidate = explicit if isinstance(explicit, str) else None
    if candidate is None:
        module = getattr(callback, "__module__", None)
        qualified = getattr(callback, "__qualname__", None)
        if (
            isinstance(module, str)
            and isinstance(qualified, str)
            and "<locals>" not in qualified
        ):
            candidate = f"{module}.{qualified}"
    if candidate is None or not 0 < len(candidate) <= MAX_REFERENCE_LENGTH:
        return None
    try:
        return candidate if import_string(candidate) is callback else None
    except (ImportError, AttributeError, TypeError):
        return None


__all__ = ["Callback", "negotiate", "wants_websocket"]
