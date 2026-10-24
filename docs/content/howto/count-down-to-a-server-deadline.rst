Count down to a server deadline
===============================

Problem
-------

Show a deadline countdown without letting client clock drift change when the action is due.

Solution
--------

Send the deadline as an epoch timestamp in milliseconds in a topic event.
Use ``Next.partial.stream.now()`` for the browser's estimate of server time.
Keep the clock zone in each update so a sequence gap can restore its current server-rendered state.

.. code-block:: javascript
   :caption: page.js

   const output = document.querySelector("[data-deadline]");
   const stop = Next.partial.stream.on(
     "auction:42",
     "auction:phase",
     ({ deadline }) => {
       const tick = () => {
         const remaining = deadline - Next.partial.stream.now();
         output.textContent = String(Math.max(0, Math.ceil(remaining / 1000)));
         if (remaining > 0) requestAnimationFrame(tick);
       };
       tick();
     },
   );

The publisher should include ``refresh(zone="deadline")`` with phase updates.
The browser then re-fetches the zone through the authorized page when it detects a missed sequence or a new epoch.
Realtime events are notifications, and the zone response carries the current state.
The stream samples ``server_time`` from its hello and patch frames and uses the median of its last five samples to estimate clock offset.

The event's ``detail`` remains unchanged.
Its topic, epoch, sequence, and server time are available as the callback's second ``meta`` argument or synchronously through ``Next.partial.stream.meta()`` during the event dispatch.

See also
--------

.. seealso::

   :doc:`/content/topics/realtime` for sequence gaps and delivery guarantees.
   :doc:`stream-live-updates-with-sse` for publishing topic updates.
