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
That round trip is the runtime's, so a page without it shows the placeholder and nothing more.
A lazy zone is the one zone mode with no server-rendered fallback, and content that has to reach a reader without JavaScript renders inline instead.

Realtime transports
-------------------

A process that mounts the ``WebSocketApp`` offers WebSocket first and Server-Sent Events as the fallback, and every other process offers Server-Sent Events alone.
The WebSocket setup is in :doc:`/content/howto/stream-over-websockets`.
Server-Sent Events flow from the server to the browser only, and a client that needs to change state over that transport uses the same forms and actions every page already has.
The WebSocket transport also carries upstream events and the form tunnel, see :ref:`topics-realtime-upstream`.
Replay is a bounded catch-up from a per-topic log, and a reconnect the log cannot cover resynchronises its zones over HTTP, see :doc:`/content/topics/realtime`.
Who is watching a topic is the job of presence, described in the Presence section of :doc:`/content/topics/realtime`.
The bridge that opens the stream is the runtime, so a page without it never subscribes and sees whatever its last full render carried.

Polling zones are client-timed
------------------------------

``poll=`` is a client timer, not a server push.
The runtime re-GETs the zone on the interval while the tab is visible, a hidden tab holds no timers, and the floor is one second.
A page without the runtime renders the body once and never ticks, so a poll adds freshness rather than delivering the value.
A change the server wants to announce the moment it happens is a stream's job, not a poll's.
See the poll section of :doc:`zones`.

One active backend
------------------

The wire format is a single project-wide choice rather than a per-request negotiation, so two protocol backends cannot serve one site.
:doc:`reference` states the rule and the warning that reports a longer list, and :doc:`extending` shows the subclass that replaces the format.

Scripts in patch HTML never run
-------------------------------

A zone's co-located assets ship on a standalone render, inline bodies and URLs alike, through the envelope's asset manifest.
A URL loads when its kind registers one of the three bundled renderers, and an inline body loads when the kind also wraps it in the element that renderer's verb builds.
A kind registered with a custom renderer reaches the browser only on a full render.
A kind whose ``inline_tag`` names another element keeps its URL form on a patch and leaves its inline bodies to the full render, and :doc:`reference` names the checks that report both.
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

Zone placement has a boundary
-----------------------------

A standalone render does not see loop variables, the condition that gated a block, a permission block, or the bindings of a ``{% with %}``.
A zone placed where it would need one is therefore undeclared for every user, and a system check reports the placement before a request reaches it.
``next.E062`` and ``next.E063`` cover a zone under a ``{% for %}``, an ``{% if %}``, or a block tag from an outside module.
``next.E161`` covers a zone under a permission decision of the page or of a component, and ``next.E065`` covers a zone in a component template.
A standalone request for such a zone answers 400 ``unknown zone``.
See :doc:`zones` for the rules and :doc:`/content/ref/system-checks` for the checks.

A dynamic body answers 400 on a zone request
--------------------------------------------

A page whose body resolves dynamically, through a ``render()`` that builds its own response, has no template for a standalone zone render.
A zone request for such a page answers 400 when no intercept serves it, so a zone belongs on a page that renders from a template.

Intercepts are selected on GET and HEAD only
--------------------------------------------

The page view picks an intercept on a GET or a HEAD alone.
A POST never selects one, so no mutation reaches an intercept through the page view.
A form or an action inside a modal posts with the ``X-Next-Zone`` of the intercept zone instead, and the server resolves the served page from the posted origin.
See :doc:`layers`.

See also
--------

.. seealso::

   :doc:`zones` for the zone tag, polling, and the keying rules.
   :doc:`sse` for the streaming transport and the refresh fan-out pattern.
   :doc:`extending` for the seams that open the protocol without forking the runtime.
   :doc:`co-located-js` for the idioms that keep behaviour alive across a morph.
