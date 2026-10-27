.. _howto-stream-live-updates-with-sse:

Stream live updates with SSE
============================

Problem
-------

A page needs to update open tabs after a mutation without polling or sending one subscriber's rendered HTML to every other subscriber.

Solution
--------

Publish a ``refresh`` patch after the mutation and return a ``TopicStream`` from an authorized stream page.
Each browser refetches the zone through its own page view, guards, and session cookies.
The page template declares the zone and a stream container that points at the stream page.

The recipe needs a root layout that renders ``{% collect_scripts %}``, because the client runtime rides that slot, see :doc:`/content/topics/static-assets/js-context`.
SSE runs under WSGI and ASGI alike, so the development server is enough.

The models
~~~~~~~~~~

.. code-block:: python
   :caption: polls/models.py

   from django.db import models

   class Poll(models.Model):
       question = models.CharField(max_length=240)

   class Choice(models.Model):
       poll = models.ForeignKey(Poll, related_name="choices", on_delete=models.CASCADE)
       text = models.CharField(max_length=120)
       votes = models.PositiveIntegerField(default=0)

The vote page
~~~~~~~~~~~~~

The page loads the poll from the URL and registers the vote form.
After the increment, ``on_valid`` publishes a ``refresh`` of the results zone to the poll's topic and answers the voter's own tab with a morph of the same zone.

.. code-block:: python
   :caption: polls/pages/polls/[int:id]/page.py

   from django.db.models import F
   from django.http import HttpRequest, HttpResponse
   from django.shortcuts import get_object_or_404
   from polls.models import Choice, Poll

   import next.forms
   from next import context
   from next.forms import redirect_to_origin
   from next.partial import Patches, is_partial_request
   from next.realtime import publish
   from next.urls import DUrl

   @context("poll")
   def poll(poll_id: DUrl["id", int]) -> Poll:
       return get_object_or_404(Poll, pk=poll_id)

   class VoteForm(next.forms.Form):
       choice = next.forms.ModelChoiceField(queryset=Choice.objects.all())

       def on_valid(self, request: HttpRequest) -> HttpResponse:
           choice = self.cleaned_data["choice"]
           Choice.objects.filter(pk=choice.pk).update(votes=F("votes") + 1)
           publish(f"poll:{choice.poll_id}", Patches(request).refresh(zone="poll-results"))
           if is_partial_request(request):
               return Patches(request).morph(zone="poll-results").response()
           return redirect_to_origin(request)

``publish`` defers the broker call to ``transaction.on_commit`` by default.
A subscriber therefore never learns of a change that rolls back.
Pass ``on_commit=False`` to publish at once, as a worker does that runs its own transaction.
A project that keeps the publish point out of the form connects a receiver to ``next.forms.signals.action_dispatched`` instead, as ``examples/live-polls`` does.

.. code-block:: jinja
   :caption: polls/pages/polls/[int:id]/template.djx

   {% zone "poll-results" %}
     <h1>{{ poll.question }}</h1>
     {% form "vote_form" %}
       {% for choice in poll.choices.all %}
         <button type="submit" name="choice" value="{{ choice.pk }}">{{ choice.text }} ({{ choice.votes }})</button>
       {% endfor %}
     {% endform %}
   {% endzone %}
   <div data-next-stream="/polls/{{ poll.pk }}/stream/"></div>

The ``data-next-stream`` element makes the runtime open the stream.

The stream page
~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: polls/realtime.py

   from polls.models import Poll

   def can_subscribe_topic(user: object, topic: str) -> bool:
       prefix, _, raw_pk = topic.partition(":")
       if prefix != "poll" or not raw_pk.isdecimal():
           return False
       return Poll.objects.filter(pk=int(raw_pk)).exists()

.. code-block:: python
   :caption: polls/pages/polls/[int:id]/stream/page.py

   from django.http import HttpRequest
   from django.shortcuts import get_object_or_404
   from polls.models import Poll
   from polls.realtime import can_subscribe_topic

   from next.realtime import TopicStream
   from next.urls import DUrl

   def render(request: HttpRequest, poll_id: DUrl["id", int]) -> TopicStream:
       poll = get_object_or_404(Poll, pk=poll_id)
       return TopicStream(request, f"poll:{poll.pk}", authorize=can_subscribe_topic)

``DUrl["id", int]`` reads the ``id`` segment of the URL as an integer, see :doc:`/content/topics/dependency-injection`.
``can_subscribe_topic`` is the ``authorize`` callback, an importable function ``(user, topic) -> bool`` that the stream rechecks while it stays open.
Omitting ``authorize`` leaves the stream open to any visitor who can load the page.

Check that it works
~~~~~~~~~~~~~~~~~~~

Open the same poll in two tabs and vote in one of them.
The voting tab morphs its zone from the form response, and the other tab receives the ``refresh`` and fetches the zone again.
The browser's network panel shows the stream request as an ``EventSource`` with ``next-hello`` first and one ``next-patches`` frame per vote.
A process that mounts the WebSocket, such as the ``runserver`` of next.dj with uvicorn installed, negotiates a socket for the same page instead, see :doc:`/content/howto/stream-over-websockets`.

The SSE endpoint is a plain ``GET`` that the session cookie authenticates.
next.dj runs no Origin check on it, while the WebSocket upgrade does.
Access therefore rests on the page guard, on ``authorize=``, and on the ``SameSite`` attribute of the session cookie.

The framework validates published envelopes and rejects HTML-bearing operations, form state, assets, and CSRF data.
The builder's request ID is carried to the stream automatically, so the tab that submitted the mutation can suppress its own echo.

The default in-memory backend shares messages only inside one process.
Install the ``broker`` extra and configure ``RedisBroker`` when the deployment has more than one worker or host.
See :doc:`/content/topics/realtime` for broker selection, transaction behavior, delivery limits, and the manual ``PatchEventStream`` escape hatch.

The complete example lives under ``examples/live-polls/``.
