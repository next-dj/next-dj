.. _topics-partial-rendering-reference:

Partial rendering reference
===========================

The patch verbs, the request and response headers, the ``data-next-*`` attributes, and the ``PARTIAL_BACKENDS`` settings, in tables.
For the narrative behind any of these, read the scenario that uses it in :doc:`scenarios`.

.. contents::
   :local:
   :depth: 1

Patch verbs
-----------

A patch is one addressed DOM operation with a verb, an optional target, optional HTML, and verb-specific extras.
The operations apply in list order.
The server is the only author of a target, the client never names one.
The envelope around the list always carries the ``assets`` and ``form`` keys, serialised as ``[]`` and ``null`` when empty, and the JSON examples in this section omit them.
``Patches(request)`` opens the builder in a handler.
``Patches.versioned(version)`` opens the same builder for code that holds no request, a test of a custom operation or a hand-assembled envelope.

.. list-table::
   :header-rows: 1
   :widths: 16 30 40 14

   * - Verb
     - Builder method
     - Semantics
     - Default
   * - ``morph``
     - ``morph()``
     - The default verb. Morph the target into the HTML. The target names a zone or a form by uid. ``extract: true`` carries a whole document the client trims to the target.
     - ``extract: false``
   * - ``replace``
     - ``replace()``
     - Replace the node wholesale, no morph.
     - none
   * - ``inner``
     - ``inner()``
     - Replace only the contents, no morph.
     - none
   * - ``append``
     - ``append()``
     - Add children at the end, dedup by ``data-next-key`` or ``id``.
     - ``dedupe: "key"``
   * - ``prepend``
     - ``prepend()``
     - Add children at the start, dedup the same way.
     - ``dedupe: "key"``
   * - ``remove``
     - ``remove()``
     - Remove the target.
     - none
   * - ``refresh``
     - ``refresh()``
     - Ask the client to re-fetch the zone with its own cookies. The safe default of an SSE fan-out.
     - none
   * - ``context``
     - ``context()``
     - Merge named serialize-provider values into ``Next.context`` and fire ``context-updated``.
     - none
   * - ``event``
     - ``event()``
     - Dispatch a ``CustomEvent`` on the document and the ``Next.on`` bus.
     - ``detail: {}``
   * - ``toast``
     - ``toast()``
     - Show a toast, sugar over ``event`` with a built-in container.
     - ``variant: "info"``
   * - ``layer.open``
     - ``layer_open()``
     - Open a layer from the server, optionally seeding a zone to fill later or an
       href whose zone loads into the modal. See :ref:`partial-server-layers`.
     - none
   * - ``layer.close``
     - ``layer_close()``
     - Close the top layer with an accept result or a dismissal.
     - accept, no result
   * - ``url``
     - ``push_url()``
     - Push browser history. The href is validated by the server. The client also honours
       ``action: "replace"``, reachable only from a raw or backend-authored envelope, the
       ``push_url()`` builder always pushes.
     - ``action: "push"``
   * - ``visit``
     - ``redirect()``
     - A full client navigation to a server-authored href. ``external=True`` skips same-host validation, see :ref:`security-overview`.
     - none

A target carries exactly one address key, and the client resolves ``zone``, then ``form``, then ``field``, then ``css``.
``zone`` names a ``data-next-zone`` wrapper and ``form`` names an action uid.
``field`` is a ``[uid, name]`` pair addressing one named input of a form by its uid.
``css`` is a raw selector, the escape hatch a bare layer shell relies on.

A verb beyond this set is registered on both sides.
``register_patch_op("confetti")`` on the server registers the name, which the ``next.E066`` check validates at ``manage.py check``, and earns the generic ``op()`` channel on the builder.
An unregistered name fails at runtime with ``UnknownPatchOpError``.
``Next.partial.defineOp("confetti", handler)`` on the client supplies the handler.
See :doc:`extending` for the end-to-end recipe, the ``context`` and ``event`` seams, and the custom-verb exceptions.

An event name that starts with ``partial:`` or ``next:``, or equals ``ready`` or ``context-updated``, is reserved for the runtime lifecycle.
``Patches.event()`` rejects such a name with ``ReservedEventNameError``, symmetric to ``op()`` rejecting a built-in verb on the generic channel, so an application cannot forge a lifecycle event.

The ``$csrf`` and ``$dev`` keys of the init payload are reserved the same way.
``Patches.context()`` rejects either name with ``ReservedContextKeyError``, and the js-context delta of a zone render drops them before it becomes a ``context`` op.
The ``$`` namespace therefore belongs to the framework on a patch exactly as it does on a full render.
A full render drops a page or component key of either name from the payload whether or not it has a framework value to write there, so no patch has a registered value to update.

Asset manifest
--------------

The ``assets`` key of an envelope lists the co-located assets the rendered targets registered.
Each entry carries ``kind`` and ``url`` always, plus ``inline`` and ``load`` when they apply.

.. list-table::
   :header-rows: 1
   :widths: 14 26 60

   * - Field
     - Value
     - Semantics
   * - ``kind``
     - A registered asset kind
     - The kind the asset was discovered under, ``css``, ``js``, ``module``, or a kind the project registered.
   * - ``url``
     - A published URL
     - The URL form of the asset. An inline asset carries an empty string here.
   * - ``inline``
     - An asset body
     - The body of an inline asset, absent on a URL-form asset.
   * - ``load``
     - ``link``, ``script``, or ``module``
     - The insertion verb, derived from the renderer registered for the kind.
       Absent when that renderer is a custom backend method, and absent on an
       inline body whose kind does not wrap it in the element the verb builds.

The runtime inserts an asset by its verb rather than by its kind, so a custom kind registered with one of the three built-in renderers loads like the built-in kind that shares it.
The verb of an entry resolves in three steps.
A ``load`` field the server wrote wins.
A URL-form entry without that field falls back to the verb the name of a built-in kind implies, ``link`` for ``css``, ``script`` for ``js``, and ``module`` for ``module``.
An envelope from a backend that spells no ``load`` field therefore still loads.
An entry carrying an inline body takes no such fallback, because the server spells the verb only when the kind wraps the body in the element the runtime builds.
A body with no explicit ``load`` is therefore dropped at the boundary rather than executed in an element a full page render prints verbatim.

An entry that resolves to no verb is skipped.
The ``next.W074`` check reports a registered kind whose renderer implies no verb, and ``next.W076`` a registered kind whose inline bodies lose the verb its URL form keeps.
Both checks walk the kinds registered in the running process, so an entry naming a kind no registration backs is skipped with no check to announce it.
See :doc:`/content/topics/static-assets/asset-kinds` for the renderer-to-verb mapping.

Request headers
---------------

Client to server.
All values are ASCII, and zone names are ASCII slugs.

.. list-table::
   :header-rows: 1
   :widths: 26 24 50

   * - Header
     - When
     - Semantics
   * - ``X-Next-Request: 1``
     - Every intercepted request
     - The partial switch. Without it the response is the full page, byte for byte.
   * - ``Accept``
     - Every intercepted request
     - ``application/vnd.next.patches+json, text/html;q=0.9``, the switch at the content-negotiation level.
   * - ``X-Next-Zone``
     - Zone GET, refresh, filter
     - The zones to render, comma-joined for a batch.
   * - ``X-Next-Validate``
     - Inline validation
     - The field names to validate without saving.
   * - ``X-Next-Merge``
     - Pagination
     - ``append`` or ``prepend``, the merge intent.
   * - ``X-Next-Version``
     - Every request once a version is learned
     - The asset version the client holds. The first request of a page asserts none.
   * - ``X-Next-Request-Id``
     - Every mutation
     - The ring id used to suppress an SSE echo.
   * - ``X-Next-Origin``
     - Every layer request, the open GET and the accept re-GET
     - The path and query string of the page that hosts a layer, for a server-side morph of its zones.
   * - CSRF header
     - Every unsafe method
     - The name comes from ``CSRF_HEADER_NAME``, the token from the runtime payload, the cookie is never read.

Response headers
----------------

Server to client.

.. list-table::
   :header-rows: 1
   :widths: 30 24 46

   * - Header
     - When
     - Semantics
   * - ``Content-Type``
     - Every envelope
     - ``application/vnd.next.patches+json``, the marker the runtime keys on.
   * - ``Vary``
     - Every partial-capable path
     - ``X-Next-Request, X-Next-Zone, X-Next-Merge, X-Next-Version``, set by the framework, so a shared cache is not poisoned.
   * - ``X-Next-Version``
     - Every envelope
     - The current asset version.
   * - ``X-Next-Form: invalid``
     - An invalid form
     - The existing invalid-form contract.
   * - ``X-Next-Action``
     - An invalid form
     - The uid of the failed action.

Status codes
------------

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Status
     - Semantics
   * - 200 with an envelope
     - Patches, including an invalid form. A validation error is state, not an HTTP failure.
   * - 200 without an envelope
     - The fetch passed through a redirect. The runtime performs a full navigation to ``response.url``.
   * - 204
     - A success with no patch to apply, for example a wizard advance with no redirect target. The runtime applies nothing.
   * - 303
     - A mutation succeeded without the runtime, the existing full cycle.
   * - 302 without an envelope
     - A guard redirect served outside the shaping path. The runtime navigates fully to the final URL.
   * - 403 without an envelope
     - A guard denial or a CSRF failure served outside the shaping path. On a mutation the runtime
       stays in place and fires ``partial:error`` with the status and body. On a safe method it
       navigates fully.
   * - 400
     - An intent that did not validate: an unknown zone, a bad origin, a zone in a dynamic page body.
   * - 404
     - An unknown form uid, the existing behaviour.
   * - 409
     - A version mismatch on a safe method, with an empty body. The runtime
       fully visits the current URL. A mutation always runs, and a version
       mismatch surfaces in the envelope version, which the client reads to
       reload once into a full client visit.
   * - 5xx
     - No envelope. The runtime swaps nothing and fires ``partial:error``.

A safe-method zone GET that answers with a non-envelope navigates once under a navigate-once flag, the same shape the version-mismatch reload uses.
A ``lazy="load"`` zone re-asks on the freshly loaded page, so an expired session, a WAF stub, or a maintenance page that keeps answering non-envelope would otherwise loop the navigation.
The second non-envelope while the flag stands degrades to a ``partial:error`` of kind ``network`` and leaves the page in place, and the flag clears the moment a correct envelope classifies.

Attributes
----------

The single namespace the runtime reads is ``data-next-*``.
The form-behaviour attributes are written by the ``{% form %}`` tag from its parameters, not hand-authored as a string DSL.

.. list-table::
   :header-rows: 1
   :widths: 26 26 48

   * - Attribute
     - On
     - Semantics
   * - ``data-next-zone``
     - Zone wrapper
     - The zone address, written by the ``{% zone %}`` tag.
   * - ``data-next-lazy``
     - Lazy zone wrapper, infinite-scroll sentinel
     - ``load`` or ``revealed``, the materialisation trigger. ``load`` fetches on
       ``ready``, ``revealed`` waits for the viewport. On a pagination sentinel
       ``revealed`` arms the observer that fires the merge GET. Any other value is
       ignored, with a console warning in dev.
   * - ``data-next-poll``
     - Zone wrapper
     - The poll interval in milliseconds, from the ``poll=`` literal, written on the
       full render and on the partial response wrapper. The runtime re-GETs the zone
       on the interval while the tab is visible. A hand-written value outside the
       whole-millisecond grammar, below the one-second floor, or above the browser
       timer ceiling is dropped, as is the attribute on an element without
       ``data-next-zone``, each with a console warning in dev.
   * - ``data-next-action``
     - ``<form>``
     - The action uid, written by ``{% form %}``, enables submit interception.
   * - ``data-next-validate``
     - ``<form>``
     - Inline validation, source is the ``validate=`` tag parameter.
   * - ``data-next-target``
     - ``<a>``, ``<form>``
     - Route the response into a zone. On a GET filter it names the zone to morph,
       and on a POST form it is written by the ``zone=`` tag parameter and travels
       as the morph target of the submission.
   * - ``data-next-trigger``
     - Filter ``<form>``, sort ``<select>``
     - The event that auto-submits a GET filter, ``input`` or ``change``. Submit and click interception are wired by ``data-next-action`` and ``data-next-merge``, not this attribute.
   * - ``data-next-debounce``
     - With ``data-next-trigger``
     - Debounce in milliseconds.
   * - ``data-next-merge``
     - Pagination link
     - ``append`` or ``prepend``, travels as ``X-Next-Merge``. Any other value is
       ignored, with a console warning in dev.
   * - ``data-next-layer``
     - ``<a>``
     - Open the href in a layer, the zone container is created before the request.
   * - ``data-next-accepted``
     - The opening ``<a>``
     - Re-fetch this zone on layer accept.
   * - ``data-next-confirm``
     - Form, link
     - A confirmation prompt before the request.
   * - ``data-next-keep``
     - Any element
     - The morph leaves the node untouched, paired by id when present and by position otherwise.
   * - ``data-next-key``
     - List rows, repeated ``<form>``
     - The match key for the morph and for ``append`` dedup, falling back to ``id``.
       On a repeated form it names the instance, written by the ``key=`` tag parameter,
       so a partial morph lands on the submitted form rather than the first.
   * - ``data-next-sse``
     - A container
     - Subscribe to a patch stream at the URL.
   * - ``data-next-busy``
     - Layer opener, layer zone container
     - Written during a layer open on the opener link and the layer's zone
       container, alongside ``aria-busy="true"``. The submit double-click guard
       is the per-uid mutation lock, not this attribute.
   * - ``data-next-dialog``
     - Runtime ``<dialog>``
     - Set by the runtime on every layer dialog, the styling hook for the modal shell.
   * - ``data-next-toasts``
     - Runtime toast container
     - The toast tray, created by the runtime on the first ``toast`` and the styling hook for the stack.
   * - ``data-next-toast``
     - Runtime toast item
     - One toast, the value is the variant, the styling hook for a single notification.

Lifecycle events
----------------

The runtime fires events on three channels, the element, the document, and the ``Next.on`` bus.
The ``next:*`` node events fire on the element as a bubbling ``CustomEvent`` caught with ``addEventListener``.
The apply-stage ``partial:*`` events and ``next:toast`` fire on the document and the ``Next.on`` bus.
A ``partial:error`` of kind ``asset``, raised when a co-located stylesheet fails to load or when the asset version still mismatches after the reload, reaches only the bus.
``ready``, ``context-updated``, ``partial:before-request``, and the fetch-stage ``partial:error`` reach only the bus.
The ``next:mounted``, ``next:removed``, and ``next:morph-*`` node events live only on ``document.addEventListener`` and never reach the bus, so ``Next.on("next:mounted")`` is a silent no-op.

.. list-table::
   :header-rows: 1
   :widths: 30 18 52

   * - Event
     - Cancelable
     - Detail
   * - ``ready``
     - No
     - The existing core contract.
   * - ``context-updated``
     - No
     - ``{context, changed}``, where ``context`` is the whole merged store and
       ``changed`` lists only the keys of the delta that arrived. The
       initial seed lists every seeded key in ``changed``.
   * - ``partial:before-request``
     - No
     - ``{url, method, intent}``, where ``intent`` is ``{zone?, uid?}``. The runtime fires this through the bus before the fetch leaves, so a listener observes the request rather than vetoing it.
   * - ``partial:before-apply``
     - Yes
     - ``{envelope}``, the op list is mutable.
   * - ``partial:applied``
     - No
     - ``{envelope, ok}``. ``ok`` is ``false`` when any op threw or named an
       unknown verb, so a listener tells a clean apply from a degraded one that
       still mounted what did change. Observe ``ok``, not the bare fact of apply.
   * - ``partial:error``
     - No
     - A discriminated union on ``kind``, where each cause carries only its own
       fields.
       ``{kind: "network", error}`` is a fetch reject, a dropped stream
       connection, or a zone that still answers a non-envelope after the
       navigate-once fallback already navigated, with no status or body to report.
       ``{kind: "http", status, body}`` is a 5xx or a mutating reply that is not
       an envelope.
       ``{kind: "parse", body, error}`` is a malformed JSON body.
       ``{kind: "op", op, error, target?}`` is a thrown or unknown verb
       mid-apply, where ``op`` names the verb and ``target`` is the
       human-readable address of the patch, present only when the op carried a
       recognised target.
       ``{kind: "asset", error, url?}`` is a stylesheet that failed to load or a
       version mismatch surviving a reload, where ``url`` is present only on a
       version mismatch.
       The ``status`` and ``body`` fields belong to ``http`` alone, and ``body``
       also to ``parse``, so a listener branches on ``kind`` before reading them.
       An ``AbortError`` never reaches this event.
   * - ``partial:layer-opened``
     - No
     - ``{opener}``
   * - ``partial:layer-accepted``
     - No
     - ``{result}``
   * - ``partial:layer-dismissed``
     - No
     - ``{reason}``
   * - ``next:mounted``
     - No
     - Fired on each touched node, bubbles. The node is the event target. Pairs with ``next:removed`` as the mount half of a framework island lifecycle.
   * - ``next:removed``
     - No
     - Fired on a node immediately before it detaches, bubbles, no detail. The unmount half of the island lifecycle, the place to tear down a mounted root or a timer.
   * - ``next:morph-element``
     - Yes
     - Fired on the old node before a pair morphs. Detail ``{newNode}``. ``preventDefault()`` skips the morph of this node and its subtree.
   * - ``next:morph-attribute``
     - Yes
     - Fired on the old element before one attribute changes. Detail
       ``{name, mutationType}``, where ``mutationType`` is ``"update"`` or ``"remove"``.
       ``preventDefault()`` skips that one attribute mutation.
   * - ``next:toast``
     - No
     - Detail ``{text, variant}``. The ``toast`` verb fires it on the document and the ``Next.on`` bus alongside building the toast.

The mount and morph events run during the patch apply, so a framework island can take over a node by vetoing its morph and managing its own subtree.
The mounted and removed pair brackets the node's life inside the document, the symmetry an adapter relies on to mount and unmount a root.

Client runtime
--------------

The runtime exposes ``window.Next`` once the bundle loads.
The surface is small, and every entry mirrors a seam the runtime already uses internally.

.. list-table::
   :header-rows: 1
   :widths: 38 62

   * - Member
     - Purpose
   * - ``Next.on(event, listener)``
     - Subscribe to a lifecycle event and receive a teardown function. A known event from the table above types its payload. A ``ready`` listener added after the runtime is ready fires at once.
   * - ``Next.use(plugin)``
     - Run a plugin function with ``Next`` and return its result, the registration point for an island adapter or a wire-format plugin.
   * - ``Next.context``
     - A frozen snapshot of the client context the server seeded and the ``context`` verb merges into.
   * - ``Next.partial.defineOp(name, handler)``
     - Register a handler for a custom verb the server authors, dispatched through the same pipeline as the built-ins.
   * - ``Next.partial.onMount(selector, callback)``
     - A re-executable mount registry. The callback runs over the matching elements at load and over every matching element a later patch inserts.
   * - ``Next.partial.parseHook(contentType, hook)``
     - Register a parser that turns a foreign content type into an envelope before the apply pipeline, so a third party can emulate another wire format.
   * - ``Next.partial.apply(raw)``
     - Parse and apply a wire envelope directly, the entry a parse hook or a test feeds.
   * - ``Next.partial.fetch(request)``
     - Send one partial request through the wire's queues and locks.
   * - ``Next.partial.layers``
     - The layer stack for driving modals from script: ``open(opener, href, zone)``, ``close(detail)``, and ``size()``.
   * - ``Next.partial.sse``
     - The stream registry: ``size()`` for the count of open connections and ``remember(id)`` to feed a request id into the echo ring.

The runtime's dev mode follows Django ``DEBUG``.
Under the default ``auto`` script injection policy a full render seeds the ``$dev`` key of the init payload while ``DEBUG`` is on.
The runtime reads that key once at bootstrap, and every diagnostic this reference calls dev-only rides it.
In dev each applied patch shows up in the browser Performance panel as a ``next:apply:<label>`` measurement and prints the same span as a ``console.debug`` line.
The label is the zone the op addresses, read the way the verb itself reads it.
``refresh`` prefers its top-level ``zone`` and falls back to the one in ``target``, ``layer.open`` reads only its top-level ``zone``, and every other verb reads ``target.zone``.
An op that names no zone that way is labelled by its verb.
Dev also counts what the envelope boundary dropped, so a malformed op and a malformed asset each earn a console warning with the number dropped instead of vanishing.
An ``ops`` or ``assets`` value that is not an array is dropped whole and earns its own console warning naming the field, since the per-entry counts would otherwise report nothing wrong.
An asset whose insertion verb the envelope boundary cannot resolve is a ``console.debug`` skip naming its kind, because a kind with a custom renderer is a normal configuration rather than damage.
A production page carries no ``$dev`` key, so it carries neither the measurements nor any of the console lines.

Intercepting modals
-------------------

A ``data-next-layer`` link opens a modal over the current view and pushes the honest URL of the modal body.
The pushed URL is the real address of the body rather than a masked URL of the page beneath it.
A refresh or a shared link resolves that URL as its own standalone page through its own ``page.py``, and Back closes the top layer.
There is no client router and no URL masking.
A single ``popstate`` handler closes the layer whose pushed URL the browser moved past.

``data-next-confirm`` and ``data-next-layer`` combine on one link.
The confirm gate is a capture-phase click handler, the layer opener is a bubble-phase one, so the confirm runs first regardless of install order.
A cancelled confirm stops the click before it reaches the opener, so the layer never opens.
An accepted confirm lets the click through and the layer opens.
The same gate protects every click-driven trigger, so a prompt fronts a layer open the same way it fronts a pagination merge.

.. _partial-server-layers:

Server-initiated layers
-----------------------

``Patches.layer_open`` opens a layer from a handler, the server counterpart of the ``data-next-layer`` opener.
Its signature is ``layer_open(*, zone=None, href=None)``, and the two keywords select one of three forms.

A layer shows a zone of a page, uniformly.
There is no separate mechanism for a whole page in a modal.
A page that opens in a layer declares a zone with ``{% zone "name" %}``, and that name travels to ``layer_open`` or to ``data-next-layer``.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Call
     - Effect
   * - ``layer_open()``
     - Open a bare modal shell. Its container carries no zone name, so only a css-targeted patch can address it. Name a zone to fill the modal with zone patches.
   * - ``layer_open(zone="cart")``
     - Open a layer whose zone container is named ``cart``, so a following ``morph(zone="cart")`` in the same envelope lands inside the modal.
   * - ``layer_open(href="/records/42/", zone="record")``
     - Fetch the ``record`` zone of ``/records/42/`` and load it into the layer. The page at that href declares ``{% zone "record" %}``.

A modal that shows a page's content takes the third form.

.. code-block:: python
   :caption: page.py

   def open_record(self, request: HttpRequest, record_id: int) -> HttpResponse:
       """Open the record's detail zone in a layer."""
       return (
           Patches(request)
           .layer_open(href=f"/records/{record_id}/", zone="record")
           .response()
       )

An href without a zone raises ``LayerHrefWithoutZoneError``.
A layer loads a zone, so an href that names no zone has nowhere to mount its content.
To open a page in a layer, wrap the page content in a zone and pass the zone name.
The href is validated same-site like every navigation sink, a cross-site value raises ``CrossSiteHrefError``.

The client ``data-next-layer="record"`` opener and the server ``layer_open(href, zone)`` do the same work, both load a page zone into a layer.

Foreign-zone authorisation
--------------------------

A modal body and a page-addressed zone ride ``X-Next-Origin`` so the server resolves the host page that owns the zone.
The server authorises that origin before rendering a foreign page's zone, raising ``ForeignPageNotAuthorizedError`` when the origin may not render it.
This keeps a page-addressed out-of-band render from reaching a zone the requester has no claim on.
A foreign page whose module fails to import raises ``PageModuleImportError`` from the same authorisation step, rather than turning the in-flight request into a 404 and dropping the patches already queued for it.

Settings
--------

The partial subsystem reads ``PARTIAL_BACKENDS`` inside ``NEXT_FRAMEWORK``.
The list holds the protocol backends, and only the first entry is active.
The rest are ignored, multi-backend selection is not supported, and a list with more than one entry earns the ``next.W071`` warning at ``manage.py check``.

.. code-block:: python
   :caption: the default

   "PARTIAL_BACKENDS": [
       {
           "BACKEND": "next.partial.PartialProtocolBackend",
           "OPTIONS": {
               "VERSION": "manifest",
               "PUSH_WIZARD_STEPS": False,
               "SSE": {
                   "HEARTBEAT_SECONDS": 25,
                   "RETRY_MS": 3000,
               },
           },
       },
   ]

.. list-table::
   :header-rows: 1
   :widths: 30 16 54

   * - Key
     - Default
     - Meaning
   * - ``VERSION``
     - ``"manifest"``
     - The source of ``X-Next-Version``. The sentinel hashes the staticfiles manifest
       when the active storage hashes its files, an explicit string overrides it, and
       without a manifest the version guard stays silent.
   * - ``PUSH_WIZARD_STEPS``
     - ``False``
     - The global default for pushing wizard steps to history. A wizard's ``Meta.push_steps`` overrides it per wizard.
   * - ``SSE.HEARTBEAT_SECONDS``
     - ``25``
     - The heartbeat period for an async source.
   * - ``SSE.RETRY_MS``
     - ``3000``
     - The ``EventSource`` reconnect hint.

See :doc:`/content/ref/settings` for every key inside ``NEXT_FRAMEWORK``.

Styling layers and toasts
-------------------------

The runtime creates a bare ``<dialog data-next-dialog>`` for every layer and a ``<div data-next-toasts>`` container for toasts.
No framework CSS is applied.
The selectors are the hook.

.. code-block:: css
   :caption: plain CSS

   [data-next-dialog] {
     width: 100%;
     max-width: 32rem;
     border-radius: 0.5rem;
     border: 1px solid hsl(var(--border));
     background-color: hsl(var(--background));
     color: hsl(var(--foreground));
     padding: 1.5rem;
     box-shadow: 0 20px 25px -5px rgb(0 0 0 / 0.1);
   }
   [data-next-dialog]::backdrop {
     background-color: rgb(0 0 0 / 0.4);
   }
   [data-next-toasts] {
     position: fixed;
     bottom: 1rem;
     right: 1rem;
     display: flex;
     flex-direction: column;
     gap: 0.5rem;
   }
   [data-next-toast] { /* default variant */ }
   [data-next-toast="success"] { /* success variant */ }

With Tailwind Play CDN ``@apply`` is available inside a ``<style type="text/tailwindcss">`` block in the layout template.

.. code-block:: jinja
   :caption: layout.djx

   <style type="text/tailwindcss">
     [data-next-dialog] {
       @apply w-full max-w-lg rounded-lg border border-border
              bg-background text-foreground shadow-xl p-6;
     }
     [data-next-dialog]::backdrop {
       @apply bg-black/40;
     }
   </style>

The ``next.dj`` examples use both patterns through the shared ``_shared/static/shared/css/base.css`` file.

See also
--------

.. seealso::

   :doc:`scenarios` for each verb, header, and attribute in the context of a task.
   :doc:`/content/ref/system-checks` for the zone and verb checks.
   :doc:`/content/topics/signals` for the partial subsystem signals.
