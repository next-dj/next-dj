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

.. code-block:: python
   :caption: polls/pages/polls/[int:id]/stream/page.py

   from django.http import HttpRequest
   from polls.deps import DPoll
   from polls.models import Poll

   from next.realtime import TopicStream

   def render(request: HttpRequest, poll: DPoll[Poll]) -> TopicStream:
       return TopicStream(request, f"poll:{poll.pk}")

The framework validates published envelopes and rejects HTML-bearing operations, form state, assets, and CSRF data.
The builder's request ID is carried to the stream automatically, so the tab that submitted the mutation can suppress its own echo.

The default in-memory backend shares messages only inside one process.
Install the ``broker`` extra and configure ``RedisBroker`` when the deployment has more than one worker or host.
See :doc:`/content/topics/realtime` for broker selection, transaction behavior, delivery limits, and the manual ``PatchEventStream`` escape hatch.

The complete example lives under ``examples/live-polls/``.
