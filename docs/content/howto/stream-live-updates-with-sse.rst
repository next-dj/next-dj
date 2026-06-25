.. _howto-stream-live-updates-with-sse:

Stream Live Updates With SSE
============================

Problem
-------

A poll page shows a vote chart and you want every open tab to update the moment someone votes, without polling and without a WebSocket stack.

Solution
--------

Return a ``PatchEventStream`` from a page module's ``render`` escape hatch and drive it from an in-process broker.
The stream carries patch envelopes as ``next-patches`` events, the same envelope an HTTP partial response carries.
Each event uses the ``refresh`` verb so every tab re-fetches the stale zone with its own
cookies through the page view, which keeps authorization in the subscriber's own view and
never broadcasts one user's HTML to another.
A full implementation lives under ``examples/live-polls/``. See :doc:`/content/misc/examples`.

This page is a quick recipe.
For the WSGI and ASGI contract, the echo suppression, and why the fan-out uses ``refresh`` rather than a context patch, read :doc:`/content/topics/partial-rendering/sse`.

Walkthrough
-----------

Build the Broker
~~~~~~~~~~~~~~~~

The broker keeps one :class:`threading.Condition` and one monotonic revision counter per poll.
``publish`` stores a fresh snapshot, records the originating request id, bumps the revision, and wakes every subscriber.
``changes`` is a generator that yields a ``Change`` value object, the snapshot plus the request id of the mutation that produced it, not wire bytes.
The framework owns the SSE framing, so the broker stays a plain pub/sub of domain events.

.. code-block:: python
   :caption: polls/broker.py

   import threading
   from collections import defaultdict
   from collections.abc import Iterator
   from dataclasses import dataclass


   @dataclass(frozen=True)
   class Change:
       snapshot: dict[str, object]
       request_id: str | None


   class PollBroker:
       def __init__(self) -> None:
           self._conditions: dict[int, threading.Condition] = defaultdict(
               threading.Condition
           )
           self._revisions: dict[int, int] = defaultdict(int)
           self._request_ids: dict[int, str | None] = {}

       def publish(self, snapshot: Snapshot, request_id: str | None = None) -> None:
           store_snapshot(snapshot)
           condition = self._conditions[snapshot.poll_id]
           with condition:
               self._revisions[snapshot.poll_id] += 1
               self._request_ids[snapshot.poll_id] = request_id
               condition.notify_all()

   broker = PollBroker()

Each subscriber captures its own ``last_revision`` before yielding, so a publish that lands while the consumer still holds a frame wakes it on the next ``next()`` instead of being lost.
A :class:`threading.Event` with ``clear()`` would drop events under fan-out, because the first subscriber clears the event before the others observe it.

.. code-block:: python
   :caption: polls/broker.py

   def changes(self, poll_id: int) -> Iterator[Change]:
       condition = self._conditions[poll_id]
       last_revision = self._revisions[poll_id]
       while True:
           current_revision = self._wait_for_new_revision(
               poll_id, condition, last_revision
           )
           if current_revision == last_revision:
               continue
           last_revision = current_revision
           payload = read_snapshot(poll_id)
           if payload is None:
               continue
           yield Change(snapshot=payload, request_id=self._request_ids.get(poll_id))

A wake timeout loops without yielding.
A sync source under WSGI sends no keepalive on its own, a documented limitation of the stream, so an idle keepalive is the source's job if a deployment needs one.

Stream From a Page Module
~~~~~~~~~~~~~~~~~~~~~~~~~~

Place a page module at the ``stream/`` route and return a ``PatchEventStream`` from ``render``.
The framework escape hatch returns any :class:`~django.http.HttpResponseBase` subclass verbatim, so no ``layout.djx`` wraps the response and the static collector does not run.
Each ``Patches`` the source yields becomes one event, and ``echo_of`` carries the originating request id so the voter's own tab drops its echo.

``DPoll`` is a project-defined DI marker the example declares in ``polls/providers.py``.
See :doc:`/content/topics/dependency-injection` for how to define custom markers.

.. code-block:: python
   :caption: polls/screens/polls/[int:id]/stream/page.py

   from collections.abc import Iterator

   from django.http import HttpRequest
   from polls.broker import broker
   from polls.models import Poll
   from polls.providers import DPoll

   from next.partial import Patches, PatchEventStream


   def patch_source(request: HttpRequest, poll_id: int) -> Iterator[Patches]:
       for change in broker.changes(poll_id):
           yield Patches(request, echo_of=change.request_id).refresh(zone="poll-results")


   def render(request: HttpRequest, poll: DPoll[Poll]) -> PatchEventStream:
       return PatchEventStream(request, patch_source(request, poll.pk))

``PatchEventStream`` sets ``Cache-Control: no-cache, no-transform`` and ``X-Accel-Buffering: no`` on construction so a proxy or ``GZipMiddleware`` does not eat the flush.
The endpoint stays sync because the broker waits on :class:`threading.Condition`, and a sync source requires WSGI.
An ASGI deployment swaps the wake primitive for an :class:`asyncio.Condition` and passes an async source, which earns a heartbeat, without touching the page or the signal layer.
The pairing is a contract.
An async source requires ASGI and a sync source requires WSGI.
A mismatch raises :exc:`~django.core.exceptions.ImproperlyConfigured` rather than silently hanging the stream.

Fan Out From the Vote Signal
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The vote handler runs the atomic ``UPDATE`` and returns its result.
A receiver on ``action_dispatched`` is the single publish point for the broker.
The signal carries the bound form after validation and the request, so the receiver knows which poll changed and which request changed it.

.. code-block:: python
   :caption: polls/signals.py

   from django import forms as django_forms
   from django.dispatch import receiver
   from django.http import HttpRequest

   from next.forms.signals import action_dispatched
   from next.partial import REQUEST_ID
   from polls.broker import broker, build_snapshot

   VOTE_ACTION_NAME = "vote_form"

   @receiver(action_dispatched)
   def broadcast_vote(
       action_name: str = "",
       form: django_forms.Form | None = None,
       request: HttpRequest | None = None,
       **_: object,
   ) -> None:
       if action_name != VOTE_ACTION_NAME or form is None:
           return
       poll = form.cleaned_data.get("poll")
       if poll is None:
           return
       request_id = request.headers.get(REQUEST_ID) if request is not None else None
       broker.publish(build_snapshot(poll), request_id=request_id)

Threading the mutation's ``X-Next-Request-Id`` to ``publish`` lets the stream echo it, so the voter's own tab drops the fan-out and applies only the morph from its own POST response.

Connect From the Browser
~~~~~~~~~~~~~~~~~~~~~~~~~

The vote page wraps its results in a zone and connects the stream with a ``data-next-sse`` element.
The runtime opens one ``EventSource`` and routes each ``next-patches`` event into the same apply pipeline an HTTP response uses, so no hand-written ``EventSource`` code is needed.

.. code-block:: jinja
   :caption: polls/screens/polls/[int:id]/template.djx

   {% zone "poll-results" %}
     {% component "poll_chart" %}
   {% endzone %}
   <div data-next-sse="/polls/{{ poll.pk }}/stream/"></div>

When the stream signals a refresh, the runtime re-fetches the ``poll-results`` zone with its own cookies, and the page view re-renders it through its own authorization.
A co-located Vue island that owns the chart re-reads ``window.Next.context`` on the zone morph and keeps its reactive state.

Verification
------------

Peek at the stream from a second terminal while a server runs.

.. code-block:: bash
   :caption: shell

   curl -N http://127.0.0.1:8000/polls/1/stream/

The ``-N`` flag disables curl output buffering so frames appear as the server flushes them.
Vote in the browser and a ``next-patches`` event arrives within milliseconds, with every open tab re-fetching its zone at the same time.

See Also
--------

.. seealso::

   :doc:`/content/topics/partial-rendering/sse` for the WSGI and ASGI contract, echo suppression, and the refresh fan-out.
   :doc:`/content/topics/signals` for the signal receiver pattern.
   :doc:`/content/topics/static-assets/asset-kinds` for registering a custom asset kind.
