.. _howto-bind-tickets-to-your-own-identity:

Bind WebSocket tickets to your own identity
===========================================

Problem
-------

A WebSocket ticket is bound to the Django session of the browser that negotiated it.
That binding fails with the signed-cookie session engine, which has no session key on a first visit and re-signs its cookie on every save.
It also fails when the identity of the browser lives in something else than a Django session, such as an API token cookie.

Solution
--------

``NEXT_FRAMEWORK['REALTIME']['TICKET_BINDING']`` names the class that decides what a ticket is bound to.
The default ``next.realtime.SessionBinding`` binds the session key, which every stored session engine keeps.
Two other routes cover the cases above.

Use the signed-cookie engine
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Select ``SignedCookieBinding`` when ``SESSION_ENGINE`` is ``django.contrib.sessions.backends.signed_cookies``.
It binds a signed-in user by the primary key and the session auth hash, and an anonymous visitor by a signed ``next_visitor`` cookie that the negotiation reply sets.
It works under any ``SESSION_ENGINE``, yet it fits the signed-cookie engine, where ``SessionBinding`` cannot.

.. code-block:: python
   :caption: config/settings.py

   SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

   NEXT_FRAMEWORK = {
       "REALTIME": {
           "TICKET_BINDING": "next.realtime.SignedCookieBinding",
       },
   }

Neither binding changes when the engine re-signs the session, so a first visit and a page that saves the session keep their sockets.
``manage.py check`` reports ``next.W142`` for the signed-cookie engine only while the binding is the default, so setting this key clears it.
The visitor cookie takes the path, domain, ``Secure`` flag, ``SameSite`` value, and lifetime of the session cookie, and it is always ``HttpOnly``.

Write a binding
~~~~~~~~~~~~~~~

A project binding subclasses ``TicketBinding`` and implements four methods.
A fifth, ``live_session``, serves SSE and has a default.
``issue(request)`` runs while the ticket is negotiated and returns the subject the ticket binds.
``credential(cookies)`` reads the credential from the cookies of the WebSocket handshake.
``subject(credential)`` maps that credential back to the subject, and the socket compares the two.
``live(credential)`` serves WebSockets and returns a ``LiveIdentity`` of the user and the seconds left, or ``None`` once the credential is gone, and it runs at the handshake and on every recheck.
``live_session(session_key)`` serves SSE, where the stream has no handshake and receives the session key of the request, and it returns the same ``LiveIdentity`` or ``None``.
It calls ``stored_session`` by default, so a binding over Django sessions leaves it alone.
``respond(request, response)`` is optional and sets what the negotiation reply carries, such as a cookie.
It reads what ``issue`` left on the request and is idempotent.

The binding below ties a socket to an API token that a first-party client keeps in the ``api_token`` cookie.
The database stores only a digest of each token, so the credential is that digest and the raw token never reaches a handler.

.. code-block:: python
   :caption: accounts/realtime.py

   import hashlib
   from collections.abc import Mapping

   from accounts.models import ApiToken
   from django.http import HttpRequest
   from django.utils import timezone

   from next.realtime import InvalidTicketError, LiveIdentity, TicketBinding

   PREFIX = "token:"

   def credential_of(raw: str | None) -> str | None:
       if not raw:
           return None
       return PREFIX + hashlib.sha256(raw.encode()).hexdigest()

   class ApiTokenBinding(TicketBinding):
       cookie_name = "api_token"

       def issue(self, request: HttpRequest) -> str:
           credential = credential_of(request.COOKIES.get(self.cookie_name))
           if credential is None or self.live(credential) is None:
               msg = "A live API token cookie is required for WebSocket tickets."
               raise InvalidTicketError(msg)
           return credential

       def credential(self, cookies: Mapping[str, str]) -> str | None:
           return credential_of(cookies.get(self.cookie_name))

       def subject(self, credential: str) -> str | None:
           return credential

       def live(self, credential: str) -> LiveIdentity | None:
           if not credential.startswith(PREFIX):
               return None
           now = timezone.now()
           token = (
               ApiToken.objects.select_related("user")
               .filter(digest=credential.removeprefix(PREFIX), expires_at__gt=now)
               .first()
           )
           if token is None or not token.user.is_active:
               return None
           return LiveIdentity(token.user, int((token.expires_at - now).total_seconds()))

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "REALTIME": {
           "TICKET_BINDING": "accounts.realtime.ApiTokenBinding",
       },
   }

``manage.py check`` reports ``next.E211`` when the path does not name a concrete ``TicketBinding`` subclass.
The framework builds one instance per path and shares it among all connections, so the class keeps no per-connection state.

Rules a binding keeps
~~~~~~~~~~~~~~~~~~~~~

``subject`` runs on the event loop of the socket, so it stays pure.
It does no database, cache, or network access, and the credential carries everything it needs.
``credential``, ``live``, and ``live_session`` run off the loop, where reading the store or the database is fine.
``issue`` may raise ``InvalidTicketError``, which answers the negotiation with ``503``, and the client then continues over SSE.
``subject`` returns ``None`` for a credential it does not recognise.

A ticket stores a digest of the subject under ``SECRET_KEY`` and is accepted under any ``SECRET_KEY_FALLBACKS`` entry too, so rotating the key does not strand a ticket for its short lifetime.
Over SSE the default ``live_session`` hands the session key of the request to ``stored_session``, which ``next.realtime`` exports.
A binding with no sessions overrides ``live_session`` to return ``None``, and one with its own SSE identity returns a ``LiveIdentity`` from it.

Limits
~~~~~~

A handler never sees the credential.
``Handshake.credential`` holds the string that the ``credential`` method returned, stays out of ``repr``, and reaches only the rechecks.
``Upstream.subject`` and ``Handshake.subject`` hold a keyed digest of what ``subject`` returns, stable across reconnects of one identity and empty when the credential proves none.
The digest changes when ``SECRET_KEY`` rotates, and under ``SessionBinding`` it follows the session rather than the user.
A handler that needs the identity reads ``upstream.user`` or ``upstream.subject``.
A stream receiver gets the same ``subject`` and ``user``.

``SignedCookieBinding`` on a server-side engine works, yet a logout in another tab does not close the open socket.
It checks the auth hash, not whether the session exists.
A password change or an inactive user closes the socket with ``4401``.
``manage.py check`` reports ``next.W147`` for that pair.
Projects on the ``db``, ``cache``, ``cached_db``, or ``file`` engine keep the default binding, which closes the socket on logout.

A view that issues tickets without ``TopicStream`` calls ``ticket_binding().respond(request, response)`` on its response, so a binding that sets a cookie, such as the visitor cookie, still sets it.
A signed-in negotiation under ``SignedCookieBinding`` deletes the visitor cookie there, so a visitor that never negotiated while signed in survives a login and a logout, and the first anonymous negotiation after one that did issues a fresh visitor.

The test helper ``issue_socket_ticket`` works with any binding.
It calls ``respond``, collects the cookies the reply sets, and stores the credential ``credential`` reads from them in ``SocketTicket.credential``.
See :doc:`/content/ref/testing`.

See :doc:`/content/ref/realtime` for the contract of each method and :doc:`/content/ref/settings` for the key.
