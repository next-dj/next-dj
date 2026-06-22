.. _ref-partial:

Partial Rendering Reference
===========================

Module Summary
--------------

``next.partial`` exposes the server side of partial rendering: the ``Patches`` builder
that authors a patch envelope, the response and stream classes that carry it, the
zone-render and origin helpers, the custom-verb registration hook, and the protocol
backend that serialises the wire format.
The wire protocol, the ``data-next-*`` attributes, and the client runtime live in the
topic section, see :doc:`/content/topics/partial-rendering/reference`.

API Tiers
---------

The surface splits into tiers that describe the intended audience for each name.
The lists below are representative.
The autodoc blocks under `Public API`_ are the exhaustive surface.

Stable.
   ``Patches``, ``PatchResponse``, ``PatchEventStream``, ``render_zone``,
   ``zone_requested``, ``is_partial_request``, ``partial_intent``,
   ``resolve_partial_origin``, ``register_patch_op``, ``UnknownZoneError``, and
   ``ForeignPageNotAuthorizedError``.
   Use these in page modules, action handlers, and stream sources.

Advanced.
   ``shape_partial``, ``PartialProtocolBackend``, ``Envelope``, ``Patch``, ``Asset``,
   ``FormMeta``, ``OriginSource``, ``ZoneInfo``, ``ZoneRenderResult``, ``zones_of``,
   the custom-verb exceptions in ``next.partial.patches``, and the ``signals`` and
   ``checks`` submodules.
   Use these when writing a custom protocol backend, a wire-format plugin, or telemetry.

Framework machinery.
   ``REQUEST_ID`` is the ``X-Next-Request-Id`` header name.
   ``PartialIntent`` and ``MergeMode`` live in ``next.partial.headers``.
   ``PartialOrigin`` lives in ``next.partial.origin``.
   ``shape_validate`` and ``drain_messages`` live in ``next.partial.shaping``.
   ``PatchOpRegistry``, the ``patch_op_registry`` instance, and ``BUILTIN_OPS`` live in
   ``next.partial.registry``.

Internal hooks.
   Underscore-prefixed helpers inside the submodules are implementation details.
   ``next.partial.__all__`` is the source of truth for the curated package surface and
   exports no underscore names.

Public API
----------

Detecting a Partial Request
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``is_partial_request`` returns ``True`` when the request carries the ``X-Next-Request``
switch, the test a ``render`` escape hatch reads before shaping a patch response.
``partial_intent`` parses and memoises the ``X-Next-*`` headers into a ``PartialIntent``.
``zone_requested`` answers whether the intent names a given zone, the guard a lazy zone's
context provider reads to skip an expensive query on a full render.

.. autofunction:: next.partial.is_partial_request

.. autofunction:: next.partial.partial_intent

.. autofunction:: next.partial.zone_requested

.. autoclass:: next.partial.headers.PartialIntent
   :members:

.. autoclass:: next.partial.headers.MergeMode
   :members:

Building Patches
~~~~~~~~~~~~~~~~~

``Patches`` is the request-bound builder.
Each method records one operation and returns ``self`` for chaining, and ``response``
finalises a ``PatchResponse`` or falls back to a redirect when the runtime is absent.
``Envelope``, ``Patch``, ``Asset``, and ``FormMeta`` are the frozen value objects the
builder assembles, surfaced for a custom backend that serialises the wire format itself.

.. autoclass:: next.partial.Patches
   :members:

.. autoclass:: next.partial.PatchResponse
   :members:

.. autoclass:: next.partial.Envelope
   :members:

.. autoclass:: next.partial.Patch
   :members:

.. autoclass:: next.partial.Asset
   :members:

.. autoclass:: next.partial.FormMeta
   :members:

Custom Verbs
~~~~~~~~~~~~

``register_patch_op`` registers a custom verb name on the server, which clears the
``next.E066`` check and earns the generic ``Patches.op`` channel.
The client supplies the handler through ``Next.partial.defineOp``.
See :doc:`/content/topics/partial-rendering/extending` for the end-to-end recipe.

.. autofunction:: next.partial.register_patch_op

Zones
~~~~~

``render_zone`` renders one or more zones of a page standalone with the full page context,
returning a ``ZoneRenderResult`` that carries the wrapped HTML and the collected assets.
``zones_of`` returns the compiled zones of a template as a mapping of ``ZoneInfo``.

.. autofunction:: next.partial.render_zone

.. autoclass:: next.partial.ZoneRenderResult
   :members:

.. autoclass:: next.partial.ZoneInfo
   :members:

.. autofunction:: next.partial.zones_of

Origin and Authorisation
~~~~~~~~~~~~~~~~~~~~~~~~~~

``resolve_partial_origin`` resolves the host page that owns a zone for an out-of-band
morph, reading the same-site ``X-Next-Origin`` header and falling back to the posted form
origin.
``OriginSource`` discriminates the two.

.. autofunction:: next.partial.resolve_partial_origin

.. autoclass:: next.partial.OriginSource
   :members:

.. autoclass:: next.partial.origin.PartialOrigin
   :members:

Shaping
~~~~~~~

``shape_partial`` turns a form action outcome into a patch envelope, the body of the
bundled form backend's partial-aware ``shape_response``.
A custom backend that overrides ``shape_response`` routes partial requests through it,
or the ``next.W068`` check warns that the runtime receives a full page instead.

.. autofunction:: next.partial.shape_partial

SSE Stream
~~~~~~~~~~

``PatchEventStream`` is a :class:`~django.http.StreamingHttpResponse` that serialises each
``Patches`` from a sync or async source as one ``next-patches`` event.
An async source requires ASGI and a sync source requires WSGI, and a mismatch raises
:exc:`~django.core.exceptions.ImproperlyConfigured` when the response is built.
See :doc:`/content/topics/partial-rendering/sse` for the WSGI and ASGI contract.

.. autoclass:: next.partial.PatchEventStream
   :members:

Protocol Backend
~~~~~~~~~~~~~~~~~

``PartialProtocolBackend`` owns the patch wire format and is the first entry of
``PARTIAL_BACKENDS``.
Subclass it and serialise a different envelope shape to support another wire format.

.. autoclass:: next.partial.PartialProtocolBackend
   :members:

Exceptions
~~~~~~~~~~

``UnknownZoneError`` is raised when a partial request names a zone the template does not
declare, surfacing as a 400 before any render.
``ForeignPageNotAuthorizedError`` is raised when an out-of-band morph of a foreign page
fails that page's own authorisation, so a zone never travels in a response the page would
have denied.
The remaining eight guard the custom-verb contract, the event-name and dedupe
vocabularies, and the foreign-page and href rules, and live in ``next.partial.patches``.

.. autoexception:: next.partial.UnknownZoneError
   :members:

.. autoexception:: next.partial.ForeignPageNotAuthorizedError
   :members:

.. autoexception:: next.partial.patches.UnknownPatchOpError
   :members:

.. autoexception:: next.partial.patches.BuiltinPatchOpError
   :members:

.. autoexception:: next.partial.patches.ReservedPatchKeyError
   :members:

.. autoexception:: next.partial.patches.UnknownContextNameError
   :members:

.. autoexception:: next.partial.patches.ReservedEventNameError
   :members:

.. autoexception:: next.partial.patches.DynamicForeignPageError
   :members:

.. autoexception:: next.partial.patches.UnknownDedupeError
   :members:

.. autoexception:: next.partial.patches.CrossSiteHrefError
   :members:

Signals
-------

See :doc:`signals` and :doc:`/content/topics/signals` for the partial signals
(``zone_registered``, ``zone_rendered``, ``patch_op_registered``, ``field_validated``,
``sse_stream_opened``, ``sse_stream_closed``).

System Checks
-------------

See :doc:`system-checks` for the zone-placement and custom-verb checks
(``next.E060`` through ``next.E066``, ``next.W067`` through ``next.W070``).

See Also
--------

.. seealso::

   :doc:`/content/topics/partial-rendering/index` for the topic subtree.
   :doc:`/content/topics/partial-rendering/reference` for the wire protocol and client runtime.
   :doc:`/content/topics/partial-rendering/extending` for custom verbs and server-pushed context.
   :doc:`settings` for ``PARTIAL_BACKENDS``.
