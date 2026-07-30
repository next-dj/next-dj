.. _topics-partial-rendering-limitations:

Limitations
===========

The partial model has a deliberate shape.
The server authors every operation, the client applies them, and the protocol stays closed.
The boundaries below follow from that shape, and each one names where the model stops and what to reach for instead.

.. contents::
   :local:
   :depth: 1

Zones render synchronously
--------------------------

A zone renders standalone over the full page context in one synchronous pass.
There is no server-side suspense flush, so a slow zone holds the response rather than streaming a placeholder and a later patch.
A ``lazy=`` zone defers its first render behind a placeholder, but the deferral is a separate client-driven round trip, not a server-held stream.

Real time is server-sent events only
------------------------------------

The streaming transport is Server-Sent Events, one direction from server to client, see :doc:`sse`.
There is no WebSocket transport, so client-to-server push over a persistent socket is outside the current model.
A client that needs to send state changes uses the same forms and actions every page already has.

Polling zones are client-timed
------------------------------

``poll=`` is a client timer, not a server push.
The runtime re-GETs the zone on the interval while the tab is visible, a hidden tab holds no timers, and the floor is one second.
A change the server wants to announce the moment it happens is a stream's job, not a poll's.
See the poll section of :doc:`zones`.

One active backend
------------------

``PARTIAL_BACKENDS`` activates its first entry and ignores the rest.
Multi-backend selection is not supported, and a list with more than one entry earns the ``next.W071`` warning at ``manage.py check``.
A different wire format is a subclass of ``PartialProtocolBackend`` installed as the single entry, see :doc:`extending`.

Scripts in patch HTML never run
-------------------------------

A zone's co-located assets ship on a standalone render, inline bodies and URLs alike, through the envelope's asset manifest.
A URL loads when its kind registers one of the three bundled renderers, and an inline body loads when the kind also wraps it in the element that renderer's verb builds.
A kind registered with a custom renderer reaches the browser only on a full render, which the ``next.W074`` check reports.
A kind whose ``inline_tag`` names another element keeps its URL form on a patch and leaves its inline bodies to the full render, which the ``next.W076`` check reports.
The full render and the patch therefore agree on which element holds a body, because an inline entry that carries no verb is dropped rather than wrapped in an element the full render would not build.
What never runs is a ``<script>`` inside the patch HTML itself, which the applier strips before the markup reaches the document.
Every asset executes once per page lifetime, so behaviour binds through the mount idioms rather than a load-time scan.
See :doc:`co-located-js` and :doc:`/content/topics/static-assets/asset-kinds`.

Patch-inserted assets carry a fixed attribute set
-------------------------------------------------

A full page render emits an asset through the backend tag templates, so ``css_tag``, ``js_tag``, and ``module_tag`` decide which attributes the element carries.
A patch has no server-rendered tag, so the runtime builds the element itself from a fixed set of attributes.
A stylesheet gets ``rel``, ``href``, and the page nonce.
A script gets the page nonce, a module additionally ``type="module"``, and both are pinned non-async so insertion order is execution order.
An attribute a project adds to its tag templates, such as ``media``, ``integrity``, ``crossorigin``, or ``defer``, therefore reaches the browser on a full render and not on a patch.
Subresource Integrity in particular does not apply to a patch-inserted asset, so a deployment that relies on it treats the assets a patch brings as outside that guarantee.
An asset the full render already emitted stays in the runtime's loaded registry and is never re-inserted, so the gap covers only assets that arrive for the first time through an envelope.
See :doc:`/content/security/static-assets` for the backend-side SRI recipe and :doc:`/content/topics/static-assets/backends` for the tag templates.

See also
--------

.. seealso::

   :doc:`zones` for the zone tag, polling, and the keying rules.
   :doc:`sse` for the streaming transport and the refresh fan-out pattern.
   :doc:`extending` for the seams that open the protocol without forking the runtime.
   :doc:`co-located-js` for the idioms that keep behaviour alive across a morph.
