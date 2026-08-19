.. _ref-partial:

next.partial API reference
==========================

Module summary
--------------

``next.partial`` exposes the server side of partial rendering.
The surface covers the ``Patches`` builder that authors a patch envelope, the response and stream classes that carry it, and the zone-render and origin helpers.
It also covers the custom-verb registration hook and the protocol backend that serialises the wire format.
The wire protocol, the ``data-next-*`` attributes, and the client runtime live in the topic section, see :doc:`/content/topics/partial-rendering/reference`.

API tiers
---------

The surface splits into tiers that describe the intended audience for each name.
The lists below are representative.
The autodoc blocks under `Public API`_ are the exhaustive surface.

Stable.
   ``Patches``, ``PatchResponse``, ``PatchEventStream``, ``render_zone``, ``ZoneRenderResult``, ``zone_requested``, ``is_partial_request``, ``partial_intent``, ``register_patch_op``, ``UnknownZoneError``, ``ForeignPageNotAuthorizedError``, and ``LayerHrefWithoutZoneError``.
   ``Envelope``, ``Patch``, ``Asset``, and ``FormMeta`` are the frozen value objects of the wire contract.
   Import all of these from ``next.partial``.
   Use them in page modules, action handlers, and stream sources.

Advanced.
   ``shape_partial`` and ``PartialProtocolBackend`` are imported from ``next.partial``.
   ``resolve_partial_origin`` stays in ``next.partial`` as a thin helper that reads the host page out of the ``X-Next-Origin`` header so a ``done`` step can pass it to ``morph(page=)``.
   ``OriginSource`` lives in ``next.partial.origin``.
   ``ZoneInfo`` and ``zones_of`` live in ``next.partial.registry``.
   The custom-verb exceptions live in ``next.partial.errors``.
   The ``signals`` and ``checks`` submodules carry the partial telemetry.
   Use these when writing a custom protocol backend, a wire-format plugin, or telemetry.

Framework machinery.
   ``REQUEST_ID`` is the ``X-Next-Request-Id`` header name and lives in ``next.partial.headers``.
   ``PartialIntent`` and ``MergeMode`` live in ``next.partial.headers``.
   ``PartialOrigin`` lives in ``next.partial.origin``.
   ``ActionRef``, ``shape_validate``, and ``drain_messages`` live in ``next.partial.shaping``.
   ``PatchOpRegistry``, the ``patch_op_registry`` instance, and ``BUILTIN_OPS`` live in ``next.partial.registry``.
   ``PartialShaperImpl``, the implementation the app binds into the :doc:`next.ports <ports>` slot at startup, lives in ``next.partial.shaper``.

Internal hooks.
   Underscore-prefixed helpers inside the submodules are implementation details.
   ``next.partial.__all__`` is the source of truth for the curated package surface and exports no underscore names.

Public API
----------

Detecting a partial request
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``is_partial_request`` returns ``True`` when the request carries the ``X-Next-Request`` switch, the test a ``render`` escape hatch reads before shaping a patch response.
``partial_intent`` parses and memoises the ``X-Next-*`` headers into a ``PartialIntent``.
``zone_requested`` answers whether the intent names a given zone, the guard a lazy zone's context provider reads to skip an expensive query on a full render.
``@context(..., zone="name")`` skips the provider call outright on a GET for another zone.
``zone_requested`` stays the in-body guard for a provider of a lazy zone, whose work has to be skipped on a full render too.

.. autofunction:: next.partial.is_partial_request

.. autofunction:: next.partial.partial_intent

.. autofunction:: next.partial.zone_requested

.. autoclass:: next.partial.headers.PartialIntent
   :members:

.. autoclass:: next.partial.headers.MergeMode
   :members:

Building patches
~~~~~~~~~~~~~~~~~

``Patches`` is the request-bound builder.
Each method records one operation and returns ``self`` for chaining, and ``response`` finalises a ``PatchResponse`` or falls back to a redirect when the runtime is absent.
``Envelope``, ``Patch``, ``Asset``, and ``FormMeta`` are the frozen value objects the builder assembles, surfaced for a custom backend that serialises the wire format itself.

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

Custom verbs
~~~~~~~~~~~~

``register_patch_op`` registers a custom verb name on the server, validated by the ``next.E066`` check, and earns the generic ``Patches.op`` channel.
An unregistered name fails at runtime with ``UnknownPatchOpError``.
The client supplies the handler through ``Next.partial.defineOp``.
See :doc:`/content/topics/partial-rendering/extending` for the end-to-end recipe.

.. autofunction:: next.partial.register_patch_op

Zones
~~~~~

``render_zone`` renders one or more zones of a page standalone with the full page context, returning a ``ZoneRenderResult`` that carries the wrapped HTML and the collected assets.
``zones_of`` returns the compiled zones of a template as a mapping of ``ZoneInfo``, both reached through ``next.partial.registry``.

.. autofunction:: next.partial.render_zone

.. autoclass:: next.partial.ZoneRenderResult
   :members:

.. autoclass:: next.partial.registry.ZoneInfo
   :members:

.. autofunction:: next.partial.registry.zones_of

Origin and authorisation
~~~~~~~~~~~~~~~~~~~~~~~~~~

``resolve_partial_origin`` is a thin helper that reads the host page that owns a zone out of the same-site ``X-Next-Origin`` header, falling back to the posted form origin, so a ``done`` step can hand the path to ``morph(page=)`` for a server out-of-band swap.
It stays importable from ``next.partial`` but sits in the Advanced tier, the canonical done choreography addresses the foreign zone through ``morph(page=, url_kwargs=)``.
``OriginSource``, which discriminates the two sources, lives in ``next.partial.origin``.

.. autofunction:: next.partial.resolve_partial_origin

.. autoclass:: next.partial.origin.OriginSource
   :members:

.. autoclass:: next.partial.origin.PartialOrigin
   :members:

Shaping
~~~~~~~

``shape_partial`` turns a form action outcome into a patch envelope, the body of the bundled form backend's partial-aware ``shape_response``.
A custom backend that overrides ``shape_response`` routes partial requests through it, or the ``next.W068`` check warns that the runtime receives a full page instead.

.. autofunction:: next.partial.shape_partial

SSE stream
~~~~~~~~~~

``PatchEventStream`` is a :class:`~django.http.StreamingHttpResponse` that serialises each ``Patches`` from a sync or async source as one ``next-patches`` event.
An async source requires ASGI and a sync source requires WSGI, and a mismatch raises :exc:`~django.core.exceptions.ImproperlyConfigured` when the response is built.
See :doc:`/content/topics/partial-rendering/sse` for the WSGI and ASGI contract.

.. autoclass:: next.partial.PatchEventStream
   :members:

Protocol backend
~~~~~~~~~~~~~~~~~

``PartialProtocolBackend`` owns the patch wire format and is the first entry of ``PARTIAL_BACKENDS``.
Subclass it and serialise a different envelope shape to support another wire format.

.. autoclass:: next.partial.PartialProtocolBackend
   :members:

Exceptions
~~~~~~~~~~

``UnknownZoneError``, ``ForeignPageNotAuthorizedError``, and ``LayerHrefWithoutZoneError`` are curated ``next.partial`` exceptions.
``UnknownZoneError`` is raised when a partial request names a zone the template does not declare, surfacing as a 400 before any render.
``ForeignPageNotAuthorizedError`` is raised when an out-of-band morph of a foreign page fails that page's own authorisation, so a zone never travels in a response the page would have denied.
``LayerHrefWithoutZoneError`` is raised when a layer seeds an ``href`` but names no ``zone=`` to load it into, so the builder refuses the layer instead of opening an empty one on the client.
The remaining nine are rarely caught and stay out of the curated surface.
They guard the custom-verb contract, the event-name, context-key, and dedupe vocabularies, and the foreign-page and href rules, and live in ``next.partial.errors``.

.. autoexception:: next.partial.UnknownZoneError
   :members:

.. autoexception:: next.partial.ForeignPageNotAuthorizedError
   :members:

.. autoexception:: next.partial.LayerHrefWithoutZoneError
   :members:

.. autoexception:: next.partial.errors.UnknownPatchOpError
   :members:

.. autoexception:: next.partial.errors.BuiltinPatchOpError
   :members:

.. autoexception:: next.partial.errors.ReservedPatchKeyError
   :members:

.. autoexception:: next.partial.errors.UnknownContextNameError
   :members:

.. autoexception:: next.partial.errors.ReservedContextKeyError
   :members:

.. autoexception:: next.partial.errors.ReservedEventNameError
   :members:

.. autoexception:: next.partial.errors.DynamicForeignPageError
   :members:

.. autoexception:: next.partial.errors.UnknownDedupeError
   :members:

.. autoexception:: next.partial.errors.CrossSiteHrefError
   :members:

Signals
-------

See :doc:`signals` and :doc:`/content/topics/signals` for the partial signals (``zone_registered``, ``zone_rendered``, ``patch_op_registered``, ``field_validated``, ``sse_stream_opened``, ``sse_stream_closed``).

System checks
-------------

See :doc:`system-checks` for the zone-placement, template-compile, custom-verb, and backend-configuration checks (``next.E060`` through ``next.E067``, ``next.E072``, ``next.E073``, ``next.W067`` through ``next.W071``).

See also
--------

.. seealso::

   :doc:`/content/topics/partial-rendering/index` for the topic subtree.
   :doc:`/content/topics/partial-rendering/reference` for the wire protocol and client runtime.
   :doc:`/content/topics/partial-rendering/extending` for custom verbs and server-pushed context.
   :doc:`settings` for ``PARTIAL_BACKENDS``.
