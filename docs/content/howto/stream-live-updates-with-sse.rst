.. _howto-stream-live-updates-with-sse:

Stream Live Updates With SSE
============================

Problem
-------

A poll page shows a vote chart and you want every open tab to update the moment someone votes, without polling and without a WebSocket stack.

next.dj adds no dedicated SSE module.
The pieces you use are the usual page module, form actions, and signals such as ``action_dispatched`` for fan-out after dispatch.
A full implementation lives under ``examples/live-polls/``. See :doc:`/content/misc/examples`.

Solution
--------

Return a :class:`~django.http.StreamingHttpResponse` with the ``text/event-stream`` content type from a page module.
Back the stream with an in-process broker built on :class:`threading.Condition` and a per-poll revision counter.
Publish a fresh snapshot from an ``action_dispatched`` receiver so the vote handler stays a plain database write.

Walkthrough
-----------

Build the broker
~~~~~~~~~~~~~~~~

The broker keeps one :class:`threading.Condition` and one monotonic revision counter per poll.
``publish`` stores the snapshot in the cache, bumps the revision, and wakes every subscriber.
``subscribe`` is a generator that yields Server-Sent Events bytes.

``Snapshot``, ``store_snapshot``, and ``read_snapshot`` are project-level helpers defined in the full example at ``examples/live-polls/polls/broker.py``.
They wrap a plain cache key so the broker stays decoupled from the cache backend.

.. code-block:: python
   :caption: polls/broker.py

   import threading
   from collections import defaultdict
   from collections.abc import Iterator
   from django.core.cache import cache

   class PollBroker:
       def __init__(self) -> None:
           self._conditions: dict[int, threading.Condition] = defaultdict(
               threading.Condition
           )
           self._revisions: dict[int, int] = defaultdict(int)

       def publish(self, snapshot: Snapshot) -> None:
           store_snapshot(snapshot)
           condition = self._conditions[snapshot.poll_id]
           with condition:
               self._revisions[snapshot.poll_id] += 1
               condition.notify_all()

   broker = PollBroker()

Each subscriber captures its own ``last_revision`` before yielding the initial snapshot.
A publish that lands while the consumer still holds that frame wakes the subscriber on the next ``next()`` instead of being lost.
A :class:`threading.Event` with ``clear()`` would drop events under fan-out because the first subscriber clears the event before the other threads observe it.

Yield SSE frames
~~~~~~~~~~~~~~~~

The first frame is always the cached snapshot under the ``snapshot`` event so a fresh tab catches up immediately.
Later frames are sent as ``update`` events.
A 15-second timeout on ``wait_for`` produces a comment frame so idle connections stay open.

.. code-block:: python
   :caption: polls/broker.py

   def subscribe(self, poll_id: int) -> Iterator[bytes]:
       condition = self._conditions[poll_id]
       last_revision = self._revisions[poll_id]
       cached = read_snapshot(poll_id)
       if cached is not None:
           yield format_event(cached, event="snapshot")
       while True:
           current_revision = self._wait_for_new_revision(
               poll_id, condition, last_revision
           )
           if current_revision == last_revision:
               yield format_keepalive()
               continue
           last_revision = current_revision
           payload = read_snapshot(poll_id)
           if payload is None:
               continue
           yield format_event(payload, event="update")

``_wait_for_new_revision`` wraps ``condition.wait_for`` with the 15-second timeout and returns the current revision.

.. code-block:: python
   :caption: polls/broker.py

   def _wait_for_new_revision(self, poll_id, condition, last_revision):
       with condition:
           condition.wait_for(
               lambda: self._revisions[poll_id] != last_revision,
               timeout=15,
           )
           return self._revisions[poll_id]

The byte formatter follows the WHATWG SSE spec, so each record ends with a blank line.
Keepalive frames begin with ``:`` so clients ignore them while proxies still see traffic.

.. code-block:: python
   :caption: polls/broker.py

   def format_event(payload: dict[str, object], *, event: str) -> bytes:
       body = json.dumps(payload, separators=(",", ":"))
       lines = [f"event: {event}"]
       lines.extend(f"data: {part}" for part in body.split("\n"))
       return ("\n".join(lines) + "\n\n").encode("utf-8")

   def format_keepalive() -> bytes:
       return b": keepalive\n\n"

Stream from a page module
~~~~~~~~~~~~~~~~~~~~~~~~~~

Place a page module at the ``stream/`` route and return the response from ``render``.
When ``render`` returns a :class:`~django.http.HttpResponseBase` subclass such as :class:`~django.http.StreamingHttpResponse`, the framework passes it through unchanged.
No ``layout.djx`` wrapping runs and the static collector does not rewrite the body.
Assets referenced only inside a streaming endpoint must still be collected from a parent page.

``DPoll`` is a project-defined DI marker the example declares in ``polls/providers.py``.
See :doc:`/content/topics/dependency-injection` for how to define custom markers.

.. code-block:: python
   :caption: polls/screens/polls/[int:id]/stream/page.py

   from django.http import StreamingHttpResponse
   from polls.broker import broker
   from polls.models import Poll
   from polls.providers import DPoll

   def render(poll: DPoll[Poll]) -> StreamingHttpResponse:
       return StreamingHttpResponse(
           broker.subscribe(poll.pk),
           content_type="text/event-stream",
           headers={
               "Cache-Control": "no-cache",
               "X-Accel-Buffering": "no",
           },
       )

The endpoint stays sync because the broker waits on :class:`threading.Condition`.
An ASGI deployment swaps the wake primitive for an :class:`asyncio.Condition` and yields from an async generator without touching the page or the signal layer.

Fan out from the vote signal
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The vote handler runs the atomic ``UPDATE`` and returns the redirect.
A receiver on ``action_dispatched`` is the single publish point for the broker.
The signal carries the bound form after validation, so the receiver knows which poll changed.

.. code-block:: python
   :caption: polls/signals.py

   from django import forms as django_forms
   from django.dispatch import receiver
   from next.forms.signals import action_dispatched
   from polls.broker import broker, build_snapshot

   VOTE_ACTION_NAME = "polls:vote"

   @receiver(action_dispatched)
   def broadcast_vote(
       action_name: str = "",
       form: django_forms.Form | None = None,
       **_: object,
   ) -> None:
       if action_name != VOTE_ACTION_NAME or form is None:
           return
       poll = form.cleaned_data.get("poll")
       if poll is None:
           return
       snapshot = build_snapshot(poll)
       broker.publish(snapshot)

Concentrating the publish in the receiver keeps the write path observable through one signal hook.

Subscribe from the browser
~~~~~~~~~~~~~~~~~~~~~~~~~~

The detail component exposes the stream URL through ``@component.context`` with ``serialize=True``, which injects the dict into ``window.Next.context.results``.
A co-located ``.vue`` single-file component reads it on mount and opens one ``EventSource``.

.. code-block:: javascript
   :caption: polls/screens/polls/[int:id]/_widgets/poll_chart/component.vue

   const ctx = window.Next?.context?.results ?? null;

   onMounted(() => {
     if (!ctx?.stream_url) return;
     source = new EventSource(ctx.stream_url);
     for (const type of ["snapshot", "update"]) {
       source.addEventListener(type, (event) => {
         applySnapshot(JSON.parse(event.data));
       });
     }
   });

Register the ``.vue`` extension as a custom asset kind from ``AppConfig.ready`` so discovery picks up the co-located file and emits a module script tag.
A raw ``.vue`` file is not browser-loadable, so the ``vue`` kind needs a custom backend whose ``register_file`` resolves the compiled module rather than the source file, see :doc:`build-a-custom-asset-backend`.

.. code-block:: python
   :caption: polls/apps.py

   from next.static import default_kinds

   class PollsConfig(AppConfig):
       name = "polls"

       def ready(self) -> None:
           default_kinds.register(
               "vue",
               extension=".vue",
               slot="scripts",
               renderer="render_module_tag",
           )

Verification
------------

Peek at the stream from a second terminal while a server runs.

.. code-block:: bash
   :caption: shell

   curl -N http://127.0.0.1:8000/polls/1/stream/

The ``-N`` flag disables curl output buffering so frames appear as the server flushes them.
The first frame is the ``snapshot`` event.
Vote in the browser and an ``update`` frame arrives within milliseconds, with every open tab updating its chart at the same time.

See Also
--------

.. seealso::

   :doc:`/content/topics/signals` for the signal receiver pattern.
   :doc:`/content/topics/static-assets/asset-kinds` for registering a custom asset kind.
