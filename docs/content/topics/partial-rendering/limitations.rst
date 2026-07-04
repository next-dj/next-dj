.. _topics-partial-rendering-limitations:

Limitations
===========

The partial model has a deliberate shape.
The server authors every operation, the client applies them, and the protocol stays closed.
The boundaries below follow from that shape, and each one names where the model stops and what to reach for instead.

.. contents::
   :local:
   :depth: 1

Zones Render Synchronously
--------------------------

A zone renders standalone over the full page context in one synchronous pass.
There is no server-side suspense flush, so a slow zone holds the response rather than streaming a placeholder and a later patch.
A ``lazy=`` zone defers its first render behind a placeholder, but the deferral is a separate client-driven round trip, not a server-held stream.

Real Time Is Server-Sent Events Only
------------------------------------

The streaming transport is Server-Sent Events, one direction from server to client, see :doc:`sse`.
There is no WebSocket transport, so client-to-server push over a persistent socket is outside the current model.
A client that needs to send state changes uses the same forms and actions every page already has.

Polling Zones Are Client-Timed
------------------------------

``poll=`` is a client timer, not a server push.
The runtime re-GETs the zone on the interval while the tab is visible, a hidden tab holds no timers, and the floor is one second.
A change the server wants to announce the moment it happens is a stream's job, not a poll's.
See the poll section of :doc:`zones`.

One Active Backend
------------------

``PARTIAL_BACKENDS`` activates its first entry and ignores the rest.
Multi-backend selection is not supported, and a list with more than one entry earns the ``next.W071`` warning at ``manage.py check``.
A different wire format is a subclass of ``PartialProtocolBackend`` installed as the single entry, see :doc:`extending`.

Scripts in Patch HTML Never Run
-------------------------------

A zone's co-located assets ship on a standalone render, inline bodies and URLs alike, through the envelope's asset manifest.
What never runs is a ``<script>`` inside the patch HTML itself, which the applier strips before the markup reaches the document.
Every asset executes once per page lifetime, so behaviour binds through the mount idioms rather than a load-time scan.
See :doc:`co-located-js`.

See Also
--------

.. seealso::

   :doc:`zones` for the zone tag, polling, and the keying rules.
   :doc:`sse` for the streaming transport and the refresh fan-out pattern.
   :doc:`extending` for the seams that open the protocol without forking the runtime.
   :doc:`co-located-js` for the idioms that keep behaviour alive across a morph.
