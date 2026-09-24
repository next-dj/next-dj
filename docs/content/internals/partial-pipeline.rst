.. _internals-partial-pipeline:

Partial pipeline
================

This page covers how a zone is compiled out of a page template, how a partial request reaches one, how an action outcome becomes a patch envelope, and what the subsystem keeps between requests.

.. contents::
   :local:
   :depth: 2

Overview
--------

``next.partial`` answers two kinds of request with the same envelope.
A zone request names one or more zones of a page and gets their re-rendered HTML back.
A form dispatch that happens to be partial gets the patches its outcome implies, which may morph a zone, morph the failed form alone, raise a toast, or drive a navigation.

Both paths build a ``Patches`` builder, turn it into an ``Envelope``, and hand that to the active protocol backend for serialisation.
Neither path is reached by an area importing ``next.partial``.
The page view and the form dispatcher both call through the ``PartialShaper`` port, which ``AppConfig.ready`` binds to ``PartialShaperImpl`` from ``next.partial.ports``.

Pipeline
--------

.. mermaid::

   flowchart TB
       Request[HTTP request] --> Intent[partial_intent parses headers]
       Intent --> View{intent.zones}
       View -- empty --> Full[Full page render]
       View -- names --> Dynamic{dynamic body}
       Dynamic -- yes --> Bad400[400 zone in dynamic body]
       Dynamic -- no --> Version{version asserted}
       Version -- mismatch on GET --> Conflict[409 empty body]
       Version -- match or absent --> Zones[zones_of composed template]
       Zones --> Batch[Drop undeclared names]
       Batch -- all unknown --> Bad400
       Batch --> Widen[Widen batch by nested zones]
       Widen --> Ctx[build_render_context for the batch]
       Ctx --> Body[render_zone_body per zone]
       Body --> Builder[Patches builder]
       Builder --> Env[Envelope]
       Env --> Backend[Protocol backend serialize]
       Backend --> Patch[PatchResponse]

Modules
-------

``next.partial.headers``.
   The request header names, the ``PartialIntent`` value object, ``partial_intent``, ``is_partial_request``, and ``set_partial_vary``.
   ``partial_intent`` parses once and memoises the result on the request, and a request without the ``X-Next-Request`` switch shares one frozen empty intent rather than building a new one.
   ``VARY_HEADERS`` names the four headers that change the body, the switch, the zone list, the merge mode, and the asserted version, while the validate, origin, and request-id headers stay out because they never change what a shared cache could store.

``next.partial.zone``.
   The ``{% zone %}`` tag, its ``ZoneOptions``, the ``ZoneNode`` that renders inline, and the ``ZonePartial`` that renders the body alone.
   ``ZonePartial`` pushes its own render-context state, which is what lets one zone body render outside the page render it was compiled in, and it delegates ``get_exception_info`` back to the page template so a ``DEBUG`` traceback still points at the real source.
   ``ZoneNode.render`` wraps the body in the addressable marker element, and a lazy zone renders only its placeholder branch with the delivery attributes that tell the client to fetch the rest.

``next.partial.registry``.
   Two unrelated records behind one address, ``ops`` for the patch verbs and ``zones`` for the zones of a compiled template.

``next.partial.render``.
   ``render_zone`` and the ``ZoneRenderResult`` it returns, which carries the wrapped HTML per zone, the bare body per zone, and the collector the bodies registered assets on.
   The two HTML forms exist because a morph addresses the wrapper and an append or prepend grafts the bare body, which would otherwise nest a second wrapper inside the live one.

``next.partial.view``.
   ``zone_response``, the branch the page view takes when the intent names zones, together with the 400 and 409 short-circuits.

``next.partial.patches``.
   The ``Patches`` builder and the ``PatchResponse`` it serialises into.
   Every verb is a method, from ``morph``, ``replace``, ``inner``, ``append``, ``prepend``, and ``remove`` to ``toast``, ``event``, ``layer_open``, ``layer_close``, ``push_url``, ``redirect``, and ``context``, plus ``morph_zone`` and ``morph_foreign_zone`` which render before they record, ``add_asset`` for an asset the render did not collect, and ``op`` for a verb the project registered.

``next.partial.envelope``.
   ``Patch``, ``Asset``, ``FormMeta``, and ``Envelope``, the value objects of the wire form.
   The module imports no request or rendering machinery, so an envelope can be built and serialised outside a request cycle, which is what the SSE path does.

``next.partial.keys``.
   The wire-key constants every area that writes a patch shares.
   The TypeScript runtime mirrors them, so a change here is a wire break that moves in lockstep with the client.

``next.partial.backends``.
   ``PartialProtocolBackend`` and the bundled ``JsonPartialProtocolBackend``, which decide the content type, the envelope serialisation, and the SSE frame format.

``next.partial.manager``.
   ``partial_backend_manager``, the single-backend façade over ``PARTIAL_BACKENDS``, and ``asset_version``, the memoised version every partial response stamps.

``next.partial.shaping``.
   The form-outcome half, split into ``outcomes`` for the routing of one ``ActionOutcome``, ``validate`` for a validate-only pass, ``scrub`` for trimming validation errors to the fields the pass asked for, ``targets`` for resolving the origin page, its zone, and the overrides a re-render needs, ``csrf`` for the rotation marker, and ``responses`` for the serialisation.

``next.partial.sse``.
   ``PatchEventStream``, the long-lived response that emits envelopes as ``next-patches`` events, see :doc:`/content/topics/partial-rendering/sse`.

``next.partial.origin``.
   ``resolve_partial_origin``, which names the host page a request morphs out of band.

``next.partial.ports``.
   ``PartialShaperImpl``, the implementation behind the ``PartialShaper`` slot.

``next.partial.errors``.
   The twelve exceptions a caller that breaks a wire contract meets, from ``UnknownZoneError`` and ``UnknownPatchOpError`` to ``ForeignPageNotAuthorizedError`` and ``CrossSiteHrefError``.

``next.partial.checks``.
   The largest checks package in the framework, split into ``backends``, ``forms``, ``ops``, ``templates``, and ``zones``, with ``codes`` holding every identifier and ``nodes`` and ``pages`` holding the node walk and the one-compile-per-page memo the zone and form checks share.

Where the view branches
-----------------------

There is no separate partial URL and no partial middleware.
A page is mounted under one view, built by ``unified_view`` in ``next.pages.manager.views``, and that view decides per request.

A page whose body comes from files on disk takes the static branch, and a page with a ``render()`` function takes the resolving branch.
Both branches do the same thing at the end.
They read the ``PartialShaper`` slot, ask it for the intent, and hand off to ``zone_response`` when ``intent.zones`` is non-empty, otherwise they render the full page and stamp the partial ``Vary`` headers on the way out.

The resolving branch resolves the page body first, so a ``render()`` that returns its own ``HttpResponse`` short-circuits before the partial branch is reached at all.
It also passes the ``dynamic`` flag through, because a body a ``render()`` produced per request has no compiled zone source to re-render, which is the 400 the zone branch answers with.

The form dispatcher branches at the same seam.
``FormActionDispatch`` shapes a partial outcome through ``shape_response`` on the port rather than by importing the shaping package, which is why ``next.forms`` does not depend on ``next.partial``.

Rendering a zone
----------------

``render_zone`` takes the page path and the requested names and performs five steps.

First it compiles the page, through ``page.composed_template_for``, which is the same composed template a full render uses, layout chain included.
Second it indexes the zones of that template with ``zones_of``.
Third it reduces the requested names to the declared ones, deduplicated in request order.
An undeclared name is dropped, so one stale name in a batch never poisons the rest, and only a batch left with nothing declared raises ``UnknownZoneError``, which the view turns into a 400.

Fourth it builds the render context for the batch.
This is where a nested zone widens it.
A zone declared inside the body of another zone renders as part of that body, so its zone-bound ``@context`` callables have to run even though the client never named it.
``ZoneInfo.nested`` holds those names, computed once when the template is first indexed, and ``_context_zone_names`` unions them into the batch before ``build_render_context`` sees it.
The context registry then skips any ``@context`` whose declared zones are disjoint from that widened set, which is what keeps a zone render from paying for the whole page's context.

Fifth it renders each zone body through ``render_zone_body``, which returns the bare body and the wrapped element, and it seeds a fresh ``StaticCollector`` so the co-located assets the bodies pull in ride back in the envelope.

The whole pass is timed only when something listens to ``zone_rendered``, so a production render with no receiver pays no clock reads.

Registries
----------

The two registries in ``next.partial.registry`` have nothing in common but their address.

``zones`` is a read-through index rather than a registration point.
``zones_of`` walks a compiled template once for its ``ZoneNode`` instances, builds a ``ZoneInfo`` per name, and memoises the mapping in a ``WeakKeyDictionary`` keyed on the template object.
A recompiled page is a different object, so it gets a fresh entry and the old one is collected with the template it belonged to.
The first read of a template announces each zone through ``zone_registered``, which is why the signal fires on a render rather than at startup.

``ops`` is an ordinary registry.
``BUILTIN_OPS`` is the frozen set of fourteen verbs the client already understands, and ``PatchOpRegistry`` records the ones a project adds through ``register_patch_op``.
Membership is what ``Patches.op`` consults, and the recorded names are what the checks read, so a registration that shadows a built-in verb is kept on record rather than dropped, which is how ``next.E066`` can report it.
The registry carries a ``version`` counter bumped on each new name.

Caches
------

Four memos sit on the partial path, each with its own lifetime.

The zone index is per compiled template and weakly held, so it lives exactly as long as the template does.

The composed page template itself is memoised by ``next.pages``, and the composition is re-read from disk only under ``DEBUG``, see :doc:`page-discovery`.
A zone render therefore costs no template compile on a warm process.

``asset_version`` is a process-wide ``functools.cache``.
Every partial response stamps it, and the resolution reads the pinned ``VERSION``, then ``STATIC_VERSION``, then the manifest branch that hashes the whole staticfiles path mapping, so it resolves once per configuration rather than once per request.
It is cleared on ``settings_reloaded`` and on a ``setting_changed`` naming ``STORAGES`` or ``STATIC_ROOT``, because the storage behind the manifest hash is configured on the Django half of the settings.

The partial intent is memoised on the request object, so the page view, the dispatcher, and a template tag all read one parse.

The checks add a fifth, the composed-pages memo in ``next.partial.checks.pages``, which keeps one compile per page for a whole check run and is dropped by ``reset_composed_pages_memo``.

Signals
-------

``zone_registered`` fires the first time a compiled template is indexed, once per zone, with the template, the name, and the lazy and poll options.
``zone_rendered`` fires once per rendered zone of a batch, with the page path, the request, and the duration.
``patch_op_registered`` fires on every ``register_patch_op`` call, including one that names a verb already on record.
``sse_stream_opened`` and ``sse_stream_closed`` bracket a patch stream, the second carrying the duration and the number of envelopes sent.
``partial_backend_loaded`` fires when the protocol backend is built from settings.

``zone_registered`` and ``zone_rendered`` are sent plainly, so a raising receiver breaks the render that emitted them.

Extension points
----------------

- Subclass ``PartialProtocolBackend`` to replace the wire format, and name it in ``PARTIAL_BACKENDS``.
- Call ``register_patch_op`` to add a verb, then emit it through ``Patches.op`` and teach the client runtime to apply it.
- Build a ``Patches`` in a page ``render()`` or an action handler and return its ``response()`` to author patches by hand.
- Return a ``PatchEventStream`` from a ``render()`` to push envelopes rather than answer a request.
- Subscribe to ``zone_rendered`` for per-zone timing without touching the render path.

See also
--------

.. seealso::

   :doc:`/content/topics/partial-rendering/how-it-works` for the request path as a user reads it.
   :doc:`/content/topics/partial-rendering/reference` for the headers, the verbs, and the status codes.
   :doc:`/content/ref/partial` for the public API.
   :doc:`request-lifecycle` for the surrounding request pipeline.
