.. _howto-count-down-to-a-server-deadline:

Count down to a server deadline
===============================

Problem
-------

Show a deadline countdown without letting client clock drift change when the action is due.

Solution
--------

Send the deadline as an epoch timestamp in milliseconds in a topic event.
Read ``Next.partial.stream.now()`` for the browser's estimate of server time.
Render the clock inside a zone and refresh that zone with every phase update.
A client that misses a sequence number then restores the current server-rendered clock.

Walkthrough
-----------

Publish the deadline
~~~~~~~~~~~~~~~~~~~~

The publisher sends one event that carries the deadline and one ``refresh`` of the clock zone.

.. code-block:: python
   :caption: auctions/realtime.py

   from auctions.models import Auction

   from next.partial import Patches
   from next.realtime import publish

   def publish_phase(auction: Auction) -> None:
       deadline = int(auction.closes_at.timestamp() * 1000)
       patches = (
           Patches(None)
           .event("auction:phase", {"deadline": deadline})
           .refresh(zone="deadline")
       )
       publish(f"auction:{auction.pk}", patches)

``publish`` defers the broker call until the surrounding transaction commits.

Declare the stream and the zone
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The page needs a stream container that points at the stream page, and a zone for the clock.
Without the container no client subscribes, and the script below never receives an event.

.. code-block:: jinja
   :caption: auctions/pages/auctions/[int:id]/template.djx

   {% zone "deadline" %}
     <output data-deadline>{{ auction.seconds_left }}</output>
   {% endzone %}
   <div data-next-stream="/auctions/{{ auction.pk }}/stream/"></div>

The stream page returns a ``TopicStream`` with an ``authorize=`` argument, as :doc:`stream-live-updates-with-sse` shows.
Omitting the argument leaves the topic open to any visitor who can load the page.

Run the countdown
~~~~~~~~~~~~~~~~~

The callback keeps one timer.
A new event cancels the previous timer, so repeated phase updates never stack loops.

.. code-block:: javascript
   :caption: page.js

   const output = document.querySelector("[data-deadline]");
   let timer = 0;
   Next.partial.stream.on(
     "auction:42",
     "auction:phase",
     ({ deadline }) => {
       clearTimeout(timer);
       const tick = () => {
         const remaining = deadline - Next.partial.stream.now();
         output.textContent = String(Math.max(0, Math.ceil(remaining / 1000)));
         if (remaining > 0) timer = setTimeout(tick, 250);
       };
       tick();
     },
   );

The browser re-fetches the zone through the authorized page when it detects a missed sequence or a new epoch.
Realtime events are notifications, and the zone response carries the current state.
The stream samples ``server_time`` from its hello and patch frames and uses the median of its last five samples to estimate the clock offset.

The callback receives the event ``detail`` as its first argument.
Its second argument, ``meta``, holds the topic, epoch, sequence, and server time.
During event dispatch, ``Next.partial.stream.meta()`` returns the same object synchronously.

See also
--------

.. seealso::

   :doc:`/content/topics/realtime` for sequence gaps and delivery guarantees.
   :doc:`stream-live-updates-with-sse` for publishing topic updates.
