.. _howto-stream-live-updates-with-sse:

Stream live updates with SSE
============================

Problem
-------

A page needs to update open tabs after a mutation without polling or sending one subscriber's rendered HTML to every other subscriber.

Solution
--------

Publish a ``refresh`` patch after the mutation commits and return a ``TopicStream`` from an authorized stream page.
Each browser refetches the zone through its own page view, guards, and session cookies.
The page template declares the zone and a stream container that points at the stream page.

.. code-block:: python
   :caption: polls/signals.py

   from django.dispatch import receiver

   from next.forms.signals import action_dispatched
   from next.partial import Patches
   from next.realtime import publish

   @receiver(action_dispatched)
   def broadcast_vote(form=None, request=None, **kwargs) -> None:
       if form is None or request is None:
           return
       poll = form.cleaned_data.get("poll")
       if poll is not None:
           publish(f"poll:{poll.pk}", Patches(request).refresh(zone="poll-results"))

``publish`` defers the broker call to ``transaction.on_commit`` by default.
A subscriber therefore never learns of a change that rolls back.
Pass ``on_commit=False`` to publish at once, as a worker does that runs its own transaction.

.. code-block:: jinja
   :caption: polls/pages/polls/[int:id]/template.djx

   {% zone "poll-results" %}
     {% component "poll_chart" %}
   {% endzone %}
   <div data-next-stream="/polls/{{ poll.pk }}/stream/"></div>

.. code-block:: python
   :caption: polls/pages/polls/[int:id]/stream/page.py

   from django.http import HttpRequest
   from polls.deps import DPoll
   from polls.models import Poll
   from polls.realtime import can_subscribe_topic

   from next.realtime import TopicStream

   def render(request: HttpRequest, poll: DPoll[Poll]) -> TopicStream:
       return TopicStream(request, f"poll:{poll.pk}", authorize=can_subscribe_topic)

``DPoll`` is the dependency marker of the example project.
``DPoll[Poll]`` injects the ``Poll`` row that the URL names, and a project defines its own marker in the same way, see :doc:`/content/topics/dependency-injection`.
``can_subscribe_topic`` is a function ``(user, topic) -> bool`` that the project writes.
Omitting ``authorize`` leaves the stream open to any visitor who can load the page.

The SSE endpoint is a plain ``GET`` that the session cookie authenticates.
next.dj runs no Origin check on it, while the WebSocket upgrade does.
Access therefore rests on the page guard, on ``authorize=``, and on the ``SameSite`` attribute of the session cookie.

The framework validates published envelopes and rejects HTML-bearing operations, form state, assets, and CSRF data.
The builder's request ID is carried to the stream automatically, so the tab that submitted the mutation can suppress its own echo.

The default in-memory backend shares messages only inside one process.
Install the ``broker`` extra and configure ``RedisBroker`` when the deployment has more than one worker or host.
See :doc:`/content/topics/realtime` for broker selection, transaction behavior, delivery limits, and the manual ``PatchEventStream`` escape hatch.

The complete example lives under ``examples/live-polls/``.
