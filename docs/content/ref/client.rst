.. _ref-client:

Client runtime reference
========================

Module summary
--------------

The client runtime is the browser half of partial rendering.
It exposes one global, ``window.Next``, and every name it owns reaches an application through that object.
The runtime applies the patch envelopes the server authors, drives the ``data-next-*`` triggers, and publishes a lifecycle event stream a page subscribes to.
It never invents a target or a swap strategy, so this page records what the runtime accepts rather than what it decides.

The TypeScript sources live in ``next/client/`` and are not part of the installed package.
``make build-js`` bundles ``next/client/next.ts`` with esbuild into ``next/static/next/next.min.js``, the single minified artefact the wheel ships.
The wheel excludes ``next/client/`` outright, so a project never imports the TypeScript and installs no Node toolchain to serve the runtime.
The script builder publishes the bundle under the static path ``next/next.min.js``, which the active staticfiles storage fingerprints like any other asset.
``next/static`` is the ``next.static`` Python package rather than an application static directory, so ``NextAppDirectoriesFinder`` keeps the framework app out of the app-directories scan and ``NextStaticFilesFinder`` is the finder that serves the bundle, see :doc:`static`.

API tiers
---------

The surface splits into tiers that describe the intended audience for each name.
A member whose name starts with an underscore is a bootstrap or test seam rather than application API.

Stable.
   ``Next.context``, ``Next.on``, ``Next.use``, ``Next.partial.onMount``, ``Next.partial.apply``, ``Next.partial.fetch``, ``Next.partial.setCsrf``, ``Next.partial.layers``, and ``Next.partial.sse``.
   The ``next:*`` element events and the ``partial:*`` document events belong to the same tier.
   Use these from a co-located asset, an island adapter, or a page script.

Extension.
   ``Next.partial.defineOp`` and ``Next.partial.parseHook`` are the two seams that widen the protocol itself.
   ``Next.use`` is the registration point a plugin built on either of them exposes.
   See :doc:`/content/topics/partial-rendering/extending` for the server half of both recipes.

Internal.
   ``Next._init`` is the bootstrap the injected inline payload calls, and ``Next.partial._configure`` and ``Next.partial._reset`` are the harness seams the unit suite drives.
   ``Next.partial.ready`` carries no underscore because the bootstrap calls it across a module boundary, and a page reaches for it only when it drives the runtime by hand.
   None of the four is an application entry point.

Public API
----------

The window global
~~~~~~~~~~~~~~~~~

The entry module ends by assigning ``window.Next``, and a ``declare global`` block widens ``Window`` so a TypeScript page sees the type.
``Next`` is a class with static members and no constructor, because one page holds one runtime.
The class itself is not exported from the bundle, so ``window.Next`` is the only handle.

.. list-table::
   :header-rows: 1
   :widths: 34 24 42

   * - Member
     - Returns
     - Description
   * - ``Next.context``
     - ``Readonly<Record<string, unknown>>``
     - A getter, not a call.
       Each read returns a frozen copy of the context the server seeded and the ``context`` verb merges into, so a held reference never sees a later merge.
   * - ``Next.on(event, listener)``
     - ``() => void``
     - Subscribe to a runtime event and receive the unsubscribe function.
       An event named in ``NextEventMap`` types its payload, and any other string falls through to an ``unknown`` payload.
   * - ``Next.use(plugin)``
     - Whatever the plugin returns
     - Call ``plugin(Next)`` and pass its result straight back, the registration point of an island adapter or a wire-format plugin.
   * - ``Next.partial``
     - ``PartialSurface``
     - The apply, fetch, layer, and stream surface, built once when the bundle evaluates.
   * - ``Next._init(context)``
     - ``void``
     - The bootstrap the injected inline payload calls once per page.
       It is not an application hook, and a page that calls it a second time reseeds the store and re-fires ``ready``.

``Next.on`` replays one event.
A ``ready`` listener registered after the runtime is already ready is called at once with the current context, so a late script does not miss the boot.
Every other event reaches only listeners registered before it fires.

A listener that throws is caught, logged through ``console.error``, and skipped, and the rest of the fan-out still runs.
A plugin author therefore reasons about failure per listener rather than per event, and a broken subscriber degrades its own feature instead of the whole page.
The bus snapshots its listener set before each fan-out, so a listener that unsubscribes mid-delivery does not disturb the round it is in.

Runtime events
~~~~~~~~~~~~~~

``NextEventMap`` is the exported interface that keys each bus payload by event name.
The table below is its whole membership.
The ``next:*`` element events fire on the DOM instead and carry no entry here, see `Document and element events`_.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Event
     - Payload
   * - ``ready``
     - ``NextContext``, the seeded context, the same value ``Next.context`` returns.
   * - ``context-updated``
     - ``{context, changed}``, where ``context`` is the whole merged store and ``changed`` lists only the keys of the delta that arrived.
       The initial seed counts as one large delta, so it lists every seeded key.
       An island reads ``changed`` to skip a re-render triggered by a foreign key.
   * - ``partial:before-request``
     - ``{url, method, intent}``, where ``intent`` is ``{zone?, uid?}``.
       The runtime fires it before the request leaves, on the bus alone, so a listener observes rather than vetoes.
   * - ``partial:before-apply``
     - ``{envelope}``, the parsed envelope with a mutable op list.
       The veto lives on the document event of the same name, not here.
   * - ``partial:applied``
     - ``{envelope, ok}``.
       ``ok`` is ``false`` when any op threw or named an unknown verb, so a listener tells a clean apply from a degraded one that still mounted what did change.
   * - ``partial:error``
     - A ``PartialError``, discriminated on ``kind`` over ``network``, ``http``, ``parse``, ``op``, and ``asset``.
       Each cause carries only its own fields, so a listener branches on ``kind`` before reading ``status`` or ``body``.
   * - ``partial:layer-opened``
     - ``{opener}``, the opening element, or ``null`` for a server-initiated open.
   * - ``partial:layer-accepted``
     - ``{result}``, the accept result the closing side passed.
   * - ``partial:layer-dismissed``
     - ``{reason}``, one of ``escape``, ``backdrop``, ``dialog``, ``popstate``, ``dismissed``, or the text a server dismiss carried.
   * - ``next:toast``
     - ``{text, variant}``, fired alongside building the toast element.

Four of these reach the bus alone.
``ready`` and ``context-updated`` have no document counterpart because the store is a runtime concern rather than a DOM one.
``partial:before-request`` fires from the fetch layer, which holds no reference to a document.
A ``partial:error`` of kind ``network``, ``http``, ``parse``, or ``asset`` also stays on the bus, and only the ``op`` kind reaches the document, because only that kind is raised from inside the apply pipeline.

Document and element events
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The runtime also dispatches ``CustomEvent`` instances on the DOM, a channel distinct from the ``Next.on`` bus.
The element events bubble and are caught with a delegated ``document.addEventListener``, and the document events are dispatched on the document directly.
A cancelable event is the only kind whose ``preventDefault()`` changes what the runtime does, and a ``Next.on`` listener for the same name cannot veto anything.
``partial:before-request`` is absent from the table because the fetch layer holds no document reference and publishes it on the bus alone.

.. list-table::
   :header-rows: 1
   :widths: 26 24 14 36

   * - Event
     - Target
     - Cancelable
     - Dispatched from
   * - ``next:mounted``
     - The mounted node, bubbles
     - No
     - The mount pass at the end of an apply, once per touched node still in the document.
   * - ``next:removed``
     - The detaching node, bubbles
     - No
     - The morph engine before it discards a node, and the layer stack before a dialog leaves the document.
   * - ``next:morph-element``
     - The old element, bubbles
     - Yes
     - The morph engine before a matched pair morphs, with detail ``{newNode}``.
       A veto skips that node and its whole subtree.
   * - ``next:morph-attribute``
     - The old element, bubbles
     - Yes
     - The morph engine before one attribute changes, with detail ``{name, mutationType}`` over ``update`` and ``remove``.
       A veto skips that one mutation.
   * - ``next:toast``
     - The document
     - No
     - The layer stack, alongside appending the toast element.
   * - ``partial:before-apply``
     - The document
     - Yes
     - The applier, before the asset gate and the ops.
       A veto abandons the whole envelope.
   * - ``partial:applied``
     - The document
     - No
     - The applier, after the ops, the asset loads, and the mount pass.
   * - ``partial:error``
     - The document
     - No
     - The applier, for a ``kind: "op"`` failure alone.
   * - ``partial:layer-opened``
     - The document
     - No
     - The layer stack, once the dialog and its zone container are in the document.
   * - ``partial:layer-accepted``
     - The document
     - No
     - The layer stack, on a close that carries a result.
   * - ``partial:layer-dismissed``
     - The document
     - No
     - The layer stack, on a browser gesture or a server dismiss.

The ``event`` patch verb dispatches a server-named ``CustomEvent`` on the document and on the bus through the same path, so an application event name joins this channel without a client registration.
:doc:`/content/topics/partial-rendering/extending` covers the server side and the reserved names the builder refuses.
The mount and morph events run inside the apply, which is what lets a framework island veto the morph of its own root and manage that subtree itself, see :doc:`/content/topics/partial-rendering/framework-islands`.

The partial surface
~~~~~~~~~~~~~~~~~~~

``Next.partial`` is a ``PartialSurface``, one object built when the bundle evaluates and never replaced.
``layers`` and ``sse`` are getters over live sub-modules, so a held reference to the surface keeps reaching the current stack and registry.

.. list-table::
   :header-rows: 1
   :widths: 44 20 36

   * - Member
     - Returns
     - Description
   * - ``apply(raw: unknown)``
     - ``Envelope``
     - Parse and apply one wire envelope, the entry a parse hook or a test feeds.
       The return is the parsed envelope rather than a completion signal, because a stylesheet in the manifest gates the ops into a continuation.
       A body that is not an object, or one carrying no ``version``, raises a ``TypeError``.
   * - ``fetch(request: WireRequest)``
     - ``Promise<void>``
     - Send one partial request through the queues and the per-uid lock.
       Network, HTTP, and parse failures surface as ``partial:error`` rather than as a rejection.
   * - ``defineOp(name: string, handler: OpHandler)``
     - ``void``
     - Register the client handler of a custom verb.
   * - ``parseHook(contentType: string, hook: ParseHook)``
     - ``void``
     - Register a parser for a foreign response content type.
   * - ``setCsrf(csrf: CsrfPayload | undefined)``
     - ``void``
     - Replace the header and token pair the next mutation submits, and clear it with ``undefined``.
       An envelope carrying a rotated token overwrites whatever was set here.
   * - ``onMount(selector: string, callback: (el: Element) => void)``
     - ``() => void``
     - Register a mount callback and receive the teardown that unregisters it.
       The callback runs over every matching element a patch inserts, and a registration made after the runtime is ready catches up over the present document at once.
   * - ``layers``
     - ``LayerStack``
     - The live stack of open layers, for driving a modal from script, see :doc:`/content/topics/partial-rendering/layers`.
   * - ``sse``
     - ``Sse``
     - The registry of open stream connections, see :doc:`/content/topics/partial-rendering/sse`.
   * - ``ready()``
     - ``void``
     - Seed the asset registry from the document, run the mount callbacks over it, then arm the triggers.
   * - ``_configure(adapters: PartialAdapters)``
     - ``void``
     - Swap the platform seams and rebuild the sub-modules, a harness seam.
   * - ``_reset()``
     - ``void``
     - Tear down every registry, timer, observer, and open layer, a harness seam.

``defineOp`` and ``parseHook`` are the two extension points, and the rest of the surface drives machinery that already exists.
``defineOp`` widens the verb vocabulary.
A handler receives the raw ``CustomPatch`` and an ``ApplyContext`` of ``dispatch``, ``mergeContext``, ``root``, and ``dev``, and a second registration under one name replaces the first.
A name the server has not registered through ``register_patch_op`` never reaches the client, and a built-in name never reaches the registry because the verb switch claims it first.

.. code-block:: javascript
   :caption: static/dashboard/confetti.js

   Next.partial.defineOp("confetti", (patch, ctx) => {
     burst(patch.count ?? 50);
     ctx.dispatch("confetti", { count: patch.count });
   });

``parseHook`` widens the wire format.
The hook owns the response body before classification and returns a value the applier parses as an envelope, so a backend answering a content type of its own is applied instead of navigated to.
The registry is keyed by the bare content type, with the charset and any other parameter stripped before the lookup.
A body no hook claims and no envelope content type covers becomes a full navigation on a safe request and a ``kind: "http"`` error on a mutation, because the action endpoint is no page to navigate to.

.. code-block:: javascript
   :caption: static/site/wire-plugin.js

   Next.use((next) => {
     next.partial.parseHook("application/vnd.example.patches+msgpack", (response, body) =>
       decode(body),
     );
     return { name: "msgpack-wire" };
   });

The attribute contract
~~~~~~~~~~~~~~~~~~~~~~

The runtime reads one attribute namespace, ``data-next-*``, and nineteen names make up the whole contract.
They fall into authorship groups, and a reader needs the split to know which are theirs to write.
The server writes five of them from ``{% zone %}`` and ``{% form %}`` parameters alone, the runtime writes four as styling and state hooks, and six are hand-authored on plain markup.
The remaining four carry both authors, written by a ``{% form %}`` parameter on a form and by hand on the link or control that drives the same behaviour without one.
``{% form %}`` reserves the ``data-next-`` prefix and raises a template syntax error on a hand-written one, so a hand-authored attribute goes on a wrapper, a link, or a submit control rather than on the tag.

.. list-table::
   :header-rows: 1
   :widths: 24 22 54

   * - Attribute
     - Author
     - Read by
   * - ``data-next-zone``
     - ``{% zone %}``
     - The applier resolves a ``zone`` target through it, the layer stack scopes that lookup per open layer, and the triggers require it on any lazy or polling element.
   * - ``data-next-lazy``
     - ``{% zone lazy= %}``
     - The triggers, which fetch a ``load`` zone on ready and hand a ``revealed`` zone to the intersection observer.
       On a pagination sentinel the value is not read and the attribute only marks the element observable.
   * - ``data-next-poll``
     - ``{% zone poll= %}``
     - The triggers, which group every zone on one interval into a single timer chain and one batched GET.
   * - ``data-next-action``
     - ``{% form %}``
     - The triggers intercept a submit and lock on the uid, and the applier resolves a ``form`` target by it.
   * - ``data-next-target``
     - ``{% form zone= %}``, hand-written on a filter form or a pagination link
     - The triggers, which read the nearest ancestor carrying it to learn the zone a request addresses.
   * - ``data-next-key``
     - ``{% form key= %}``, hand-written on a list row
     - The morph engine pairs nodes by it, the merge verbs deduplicate rows by it before falling back to ``id``, and the triggers send it so a response lands on the submitted form instance.
   * - ``data-next-validate``
     - ``{% form validate= %}``
     - The triggers, which probe the blurred field with a validate-only POST.
       Presence alone switches the behaviour on and the value is never read.
   * - ``data-next-trigger``
     - ``{% form trigger= %}``, hand-written on a sort control
     - The triggers, which auto-submit a GET filter when the named event type fires.
   * - ``data-next-debounce``
     - ``{% form debounce= %}``, hand-written beside a trigger
     - The triggers, which collapse a burst of events into the last one.
   * - ``data-next-merge``
     - Hand-written on a pagination link or sentinel
     - The triggers, which GET the link with an ``append`` or ``prepend`` intent so the server authors the merge patch.
   * - ``data-next-confirm``
     - Hand-written on a link or a submit control
     - The triggers, which run the confirm gate on the nearest ancestor carrying it and cancel the interaction on a refusal.
   * - ``data-next-keep``
     - Hand-written on any element
     - The morph engine, which leaves the node and its subtree untouched so a mounted island root survives.
   * - ``data-next-sse``
     - Hand-written on a container
     - The stream bridge, which opens one ``EventSource`` per distinct URL and rescans each inserted subtree.
   * - ``data-next-layer``
     - Hand-written on an ``<a>``
     - The layer stack, which opens the href in a modal and names the zone the dialog hosts.
       A link missing either the zone or the href stays a plain navigation.
   * - ``data-next-accepted``
     - Hand-written on the opening ``<a>``
     - The layer stack, which re-fetches the named zone of the host page once the layer closes with a result.
   * - ``data-next-busy``
     - The runtime
     - The layer stack writes it on the opener and the target zone for the span of a request, alongside ``aria-busy="true"``, and reads it back as the double-click guard.
   * - ``data-next-dialog``
     - The runtime
     - Nothing in the runtime reads it.
       It marks every layer ``<dialog>`` as the styling hook of the modal shell.
   * - ``data-next-toasts``
     - The runtime
     - Nothing in the runtime reads it.
       It marks the toast tray, which also carries ``aria-live="polite"``.
   * - ``data-next-toast``
     - The runtime
     - Nothing in the runtime reads it.
       It marks one toast item and its value is the variant.

:doc:`/content/topics/partial-rendering/reference` records the accepted values, the closed value sets, and the dev warnings each attribute earns.
:doc:`template-tags` records the ``{% zone %}`` and ``{% form %}`` parameters that compile to the nine names a tag can write.

Exported types
~~~~~~~~~~~~~~

The entry module exports four type names.
Two are its own and two are re-exported from the protocol module, which is where the client and the server agree on the wire vocabulary.
A page written in TypeScript reaches them through the bundle's declaration output rather than by importing ``next/client/``.

.. code-block:: typescript
   :caption: the exported surface of next/client/next.ts

   export type NextContext = Readonly<Record<string, unknown>>;

   export interface NextEventMap {
     ready: NextContext;
     "context-updated": { context: NextContext; changed: string[] };
     "partial:before-request": {
       url: string;
       method: string;
       intent: { zone?: string; uid?: string };
     };
     "partial:before-apply": { envelope: Envelope };
     "partial:applied": { envelope: Envelope; ok: boolean };
     "partial:error": PartialError;
     "partial:layer-opened": { opener: HTMLElement | null };
     "partial:layer-accepted": { result: unknown };
     "partial:layer-dismissed": { reason: string };
     "next:toast": { text: string; variant: string };
   }

   export type { PartialError, PartialErrorKind } from "./protocol";

``PartialError`` is the discriminated union every ``partial:error`` payload inhabits, over the ``network``, ``http``, ``parse``, ``op``, and ``asset`` kinds.
``PartialErrorKind`` is its discriminant, aliased for a listener that switches on it.
``Envelope`` and ``PartialSurface`` are declared in the apply and partial modules rather than re-exported here, so their names reach a reader through this page rather than through an import.

The plugin shape ``Next.use`` accepts stays module-local and carries no export.
It is ``(next: typeof Next) => T``, a single function of the facade returning whatever the plugin wants to hand back.
A plugin therefore needs no type import to be written, and its result type flows out of ``Next.use`` unchanged.

The module graph
----------------

One module graph sits behind the facade, and the table below names the piece each concern lives in.
None of these modules is reachable from application code, so the names serve reading a stack trace rather than writing against them.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Module
     - Concern
   * - ``next.ts``
     - The ``Next`` facade, the context store, the event bus, the plugin hook, and the bundle entry point.
   * - ``partial.ts``
     - Assembly of the ``Next.partial`` surface over the other modules and their injectable platform seams.
   * - ``apply.ts``
     - Envelope parsing, the built-in patch verbs, the custom-verb registry, and script neutralisation before insertion.
   * - ``wire.ts``
     - Request shaping, intent and CSRF headers, response classification, the per-target GET queues, and the per-uid mutation lock.
   * - ``morph.ts``
     - The morph engine that reuses live nodes so focus, caret, typed values, and scroll survive a patch.
   * - ``layers.ts``
     - Modal layers over the native ``<dialog>``, top-down target resolution, and the toast tray.
   * - ``triggers.ts``
     - The delegated ``data-next-*`` handlers, lazy zone activation, the pollers, and inline validation.
   * - ``sse.ts``
     - The Server-Sent Events bridge, its echo ring of own request ids, and the pause and resume on tab visibility.
   * - ``assets.ts``
     - The asset loader, the registry of URLs already on the page, and the asset version safeguard.
   * - ``dirty.ts``
     - The touched-element tracker whose snapshots keep a response from overwriting what the user typed.
   * - ``adapters.ts``
     - Default platform adapters over the browser globals a test harness replaces.
   * - ``protocol.ts``
     - The wire vocabulary shared with the server, the content type, the headers, and the ``PartialError`` union.

Configuration
-------------

Nothing configures the runtime from the browser side.
``NEXT_JS_OPTIONS`` inside ``NEXT_FRAMEWORK`` decides how the bundle and the inline ``_init`` payload reach the page, over the ``auto``, ``manual``, and ``disabled`` injection policies and the three tag templates.
``JS_CONTEXT_SERIALIZER`` decides how a context value is encoded on its way into ``Next.context``.
See :doc:`settings` for both keys and :doc:`/content/topics/static-assets/js-context` for the full option table.

The runtime's dev channel follows Django ``DEBUG`` rather than a client switch.
Under the ``auto`` policy a full render seeds the ``$dev`` key of the init payload while ``DEBUG`` is on, and ``_init`` opens the channel from that key before the first trigger scan.
A production page carries no such key, so it carries neither the per-op performance measurements nor any of the console diagnostics.

See also
--------

.. seealso::

   :doc:`/content/topics/partial-rendering/index` for the topic subtree the narrative lives in.
   :doc:`/content/topics/partial-rendering/reference` for the envelope fields, the patch verbs, and the headers.
   :doc:`/content/topics/partial-rendering/co-located-js` for the idioms a page script follows to survive a patch.
   :doc:`partial` for the server side that authors every envelope the runtime applies.
   :doc:`/content/contributing/quality-gates` for how the bundle is built and what holds its size.
