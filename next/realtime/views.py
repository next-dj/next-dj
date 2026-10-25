"""The opt-in endpoint a WebSocket gateway outside Python calls to check a ticket."""

import json
import logging
from typing import Any

from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, JsonResponse
from django.utils.cache import add_never_cache_headers
from django.utils.crypto import constant_time_compare
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from next.conf.scopes import scope_value

from .access import cookie_session_key, live_session
from .errors import InvalidTicketError
from .tickets import TicketClaims, resolve_claims, verify_ticket


logger = logging.getLogger(__name__)

_DENIED = {"error": "denied"}
_INVALID = {"error": "invalid"}


def _answer(body: dict[str, Any], status: int) -> JsonResponse:
    """Return a JSON body no cache may keep."""
    response = JsonResponse(body, status=status)
    add_never_cache_headers(response)
    return response


def _fields(request: HttpRequest) -> tuple[str, str] | None:
    """Read the ticket and the forwarded Cookie header from a JSON body."""
    try:
        body = json.loads(request.body)
    except (ValueError, RecursionError):
        return None
    match body:
        case {"ticket": str() as ticket, "cookie": str() as cookie}:
            return ticket, cookie
        case _:
            return None


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(login_not_required, name="dispatch")
class TicketIntrospectionView(View):
    """Verify a WebSocket ticket for a gateway and return the grant it may serve.

    A malformed body answers 400 after the bearer check, any refusal one 403 body.
    """

    http_method_names = ("post",)

    def authorize(self, request: HttpRequest) -> bool:
        """Accept the `REALTIME['GATEWAY_TOKEN']` bearer, compared in constant time."""
        token = scope_value("REALTIME", "GATEWAY_TOKEN")
        if not isinstance(token, str) or not token:
            return False
        scheme, _, supplied = request.headers.get("Authorization", "").partition(" ")
        return scheme.lower() == "bearer" and constant_time_compare(
            supplied.strip(), token
        )

    def grant(
        self, claims: TicketClaims, user: object, expires_in: int | None
    ) -> dict[str, Any]:
        """Return the body of an accepted ticket, never the session key or its data.

        `expires_in` is left out when the session store keeps no deadline it can read.
        """
        signed_in = getattr(user, "is_authenticated", False) is True
        pk = getattr(user, "pk", None) if signed_in else None
        body: dict[str, Any] = {
            "topics": list(claims.topics),
            "user": None if pk is None else str(pk),
        }
        if expires_in is not None:
            body["expires_in"] = expires_in
        return body

    def grant_transformed(
        self, claims: TicketClaims, user: object, expires_in: int | None
    ) -> dict[str, Any] | None:
        """Return the body of an accepted ticket naming a transform, or None to refuse.

        A gateway outside Python cannot run the transform, so the default refuses.
        """
        del claims, user, expires_in
        return None

    def post(self, request: HttpRequest) -> JsonResponse:
        """Answer the grant of a live ticket, or the one refusal for anything else."""
        if not self.authorize(request):
            return _refuse("The caller is not authorized.")
        fields = _fields(request)
        if fields is None:
            return _answer(_INVALID, 400)
        ticket, cookie = fields
        session_key = cookie_session_key(cookie) or ""
        try:
            claims, user, expires_in = _vouch(ticket, session_key)
        except InvalidTicketError as exc:
            return _refuse(str(exc))
        if claims.transform_ref is None:
            return _answer(self.grant(claims, user, expires_in), 200)
        body = self.grant_transformed(claims, user, expires_in)
        if body is None:
            return _refuse("The ticket names a transform the view does not serve.")
        return _answer(body, 200)


def _vouch(ticket: str, session_key: str) -> tuple[TicketClaims, object, int | None]:
    """Verify a ticket against its live session and the authorizer it names."""
    claims = verify_ticket(ticket, session_key)
    _transform, authorizer = resolve_claims(claims)
    live = live_session(session_key)
    if live is None:
        msg = "The ticket's session is gone."
        raise InvalidTicketError(msg)
    user, expires_in = live
    if any(authorizer(user, topic) is not True for topic in claims.topics):
        msg = "The topic authorizer denies the ticket."
        raise InvalidTicketError(msg)
    return claims, user, expires_in


def _refuse(reason: str) -> JsonResponse:
    """Log why a request was refused and answer the uniform denial."""
    logger.info("Refused a ticket introspection request. %s", reason)
    return _answer(_DENIED, 403)


__all__ = ["TicketIntrospectionView"]
