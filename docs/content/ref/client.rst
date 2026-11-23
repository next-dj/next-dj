.. _ref-client:

Client runtime reference
========================

Module summary
--------------

The client runtime is the browser half of partial rendering.
It exposes one global, ``window.Next``, and every name it owns reaches an application through that object.
The runtime applies the patch envelopes the server authors, drives the ``data-next-*`` triggers, and publishes a lifecycle event stream a page subscribes to.
It never invents a target or a swap strategy, so this page records what the runtime accepts rather than what it decides.

The core bundle loads on every page, and six lazy chunks load only for a page that needs them.

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Chunk
     - Carries and fetched when
   * - ``scripts``
     - Consent and third-party scripts, fetched only for a page that needs them, see :doc:`client-extras`.
   * - ``stream``
     - The shared stream state and the SSE transport, fetched once a scan finds a ``data-next-stream`` container.
   * - ``ws``
     - The WebSocket transport, its upstream events, and the request tunnel, fetched only when the server enables WebSockets for a stream.
   * - ``csrf``
     - The fetch of a deferred CSRF token, fetched on the first need of a page that shipped only the endpoint.
   * - ``poll``
     - The zone poller, fetched once a scan finds a ``data-next-poll`` zone.
   * - ``dev``
     - The diagnostics of a ``DEBUG`` render, never fetched by a production page.

Declared bundles are a second kind of lazy code.
The server renders a script tag for each one a page needs, right after the runtime, and the bundle registers with the runtime as it evaluates, so no chunk list names it and ``Next.ready`` does not fetch it.

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Bundle
     - Carries and declared when
   * - ``defer``
     - The fetch of client-trip boundaries, the ``data-next-fetch`` and ``data-next-retry`` controls, the token expiry reload, and the second fetch of an ``incomplete`` boundary, declared by a page with a client-trip boundary or an error UI that offers a retry.
       Once it loads, ``Next.partial.fetch`` also takes a boundary wrapper element and fetches that boundary.
   * - ``reload``
     - The loading UI of a ``reload="loading"`` zone and the skeletons of the boundaries inside it, and the loading UI of a layer before its answer, declared by a page with such a zone or with a layer link whose zone has one.
   * - ``more``
     - The pruning, the address writes, and the retries of ``{% more %}``, declared by a sentinel that sets ``prune=`` or ``url=``.
   * - ``reader``
     - The reader of a streamed page or partial response, declared by a streamed page and by a page whose partial responses may stream.
   * - ``router``
     - The client router, declared by every routed document while ``ROUTER["ENABLED"]`` holds and loaded as a plain ``async`` script, since the first click needs it.
       Once it evaluates, ``Next.router`` exists, see `The router`_.
   * - ``history``
     - The snapshots, the scroll restore, the scroll after a push, the memo of URLs that answered no envelope, the memo of shared pages a navigation fetches as plain documents, the default focus move and announcement, persisted elements, view transitions, the retirement of page stylesheets, the inline scripts a navigation runs, the navigation of a ``GET`` form, and the reading of a ``text/html`` answer under ``ROUTER["FOREIGN"] = "extract"``.
       Named by URL in the ``$router`` payload entry rather than by a tag, and loaded by the router once the page is idle or two seconds have passed, at once when the document opens on a history entry the router wrote before, and before any navigation or ``Next.router.prefetch()`` applies, which waits fifteen seconds at most.
   * - ``intent``
     - The intent and viewport prefetch of links and layers, the cache of prefetched answers, and the client pass of active links, named and loaded as the ``history`` bundle is.

The load attributes of each tag are listed under ``NEXT_JS_OPTIONS`` in :doc:`settings`, and the ``disabled`` script policy renders none, which ``next.W181`` reports.

A chunk that fails to load, or has not loaded within 15 seconds, fires ``partial:error`` of kind ``asset`` once and is fetched again on its next need, three attempts in all, so a stalled request holds no ``Next.ready``, form submit, or stream for good.
The ``history``, ``intent``, and ``reader`` bundles of the router fail the same way and share the three attempts of the router, which requests them again.
A chunk that lands after its timeout is still delivered to every subscriber that waited for it.
The TypeScript sources are not part of the installed package, so a project imports no TypeScript and installs no Node toolchain.
:doc:`/content/contributing/quality-gates` describes how the bundles are built and budgeted, and :doc:`static` describes the finder that serves them.

API tiers
---------

The surface splits into tiers that describe the intended audience for each name.
A member whose name starts with an underscore is a bootstrap or test seam rather than application API.

Stable.
   ``Next.context``, ``Next.on``, ``Next.use``, ``Next.ready``, ``Next.navigation``, ``Next.router``, ``Next.consent``, ``Next.scripts``, ``Next.partial.onMount``, ``Next.partial.mount``, ``Next.partial.apply``, ``Next.partial.fetch``, ``Next.partial.submit``, ``Next.partial.setCsrf``, ``Next.partial.layers``, and ``Next.partial.stream``.
   The ``next:*`` events, the ``partial:*`` document events, and the ``router:*`` events belong to the same tier.
   Use these from a co-located asset, an island adapter, or a page script.

Extension.
   ``Next.partial.defineOp`` and ``Next.partial.parseHook`` are the two seams that widen the protocol itself.
   ``Next.use`` is the registration point a plugin built on either of them exposes.
   See :doc:`/content/topics/partial-rendering/extending` for the server half of both recipes.

Internal.
   ``Next._init`` is the bootstrap the injected inline payload calls, ``Next._land`` is the handshake every lazy chunk calls as it evaluates and ignores a key the runtime does not know, ``Next.partial._current`` backs ``Next.navigation.current``, and ``Next.partial._configure`` and ``Next.partial._reset`` are the harness seams the unit suite drives.
   ``Next.partial.ready`` carries no underscore because the bootstrap calls it across a module boundary, and a page reaches for it only when it drives the runtime by hand.
   None of the six is an application entry point.

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
   * - ``Next.navigation.current()``
     - ``NavigationState``
     - Where the page stands, ``{url, path, title}``.
   * - ``Next.router``
     - ``Router``
     - The client router, ``undefined`` until the ``router`` bundle has evaluated and on a page the router does not serve, see `The router`_.
   * - ``Next.ready(chunk)``
     - ``Promise<NextChunks[K]>``
     - Resolve with the surfaces of a lazy chunk once it has loaded and taken the init payload, fetching it when needed, and reject when it cannot load.
       ``"scripts"`` is the one chunk, see :doc:`client-extras`.
   * - ``Next.consent``, ``Next.scripts``
     - See :doc:`client-extras`
     - The surfaces of the scripts chunk, ``undefined`` until it loads.
   * - ``Next._init(context)``
     - ``void``
     - The bootstrap the injected inline payload calls once per page.
       It is not an application hook, and a page that calls it a second time reseeds the store and re-fires ``ready``.

``Next.on`` replays one event to a listener that subscribes late.
``ready`` describes a state rather than a moment, so a ``ready`` listener registered after the runtime is already ready is called at once with the current context.
Every other event reaches only listeners registered before it fires, ``next:consent`` included, and a ``document`` listener never receives a replay.

A listener that throws is caught, logged through ``console.error``, and skipped, and the rest of the fan-out still runs.
A plugin author therefore reasons about failure per listener rather than per event, and a broken subscriber degrades its own feature instead of the whole page.
The bus snapshots its listener set before each fan-out, so a listener that unsubscribes mid-delivery does not disturb the round it is in.

Runtime events
~~~~~~~~~~~~~~

``NextEventMap`` is the exported interface that keys each bus payload by event name.
The table below lists every event of the bus.
The ``next:mounted``, ``next:removed``, and ``next:morph-*`` element events fire on the DOM instead and carry no entry here, see `Document and element events`_.

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
       The runtime fires it before the request leaves, and the event is not cancelable, so a listener observes rather than vetoes.
   * - ``partial:before-apply``
     - ``BeforeApply``, ``{envelope, boundaries, hold}``, the parsed envelope with a mutable op list, the boundary wrappers its ops touch, and ``hold()``.
       ``hold()`` returns a release function, and the ops of this envelope and of every later one wait until each hold taken is released, so an exit animation of a boundary finishes first.
       The veto lives on the document event of the same name, not here.
   * - ``partial:applied``
     - ``{envelope, ok, nodes, stale?}``.
       ``ok`` is ``false`` when any op threw or named an unknown verb, so a listener tells a clean apply from a degraded one that still mounted what did change.
       ``nodes`` are the elements the ops inserted or morphed, the roots the mount pass ran over.
       ``stale`` is ``true`` when a newer request superseded the envelope while its stylesheets loaded, so no op ran, ``ok`` is ``false``, and ``nodes`` is empty.
   * - ``partial:error``
     - A ``PartialError``, discriminated on ``kind`` over ``network``, ``timeout``, ``http``, ``parse``, ``op``, ``asset``, ``csrf``, ``undelivered``, and ``incomplete``, with the ``zones`` of the request when it addressed any.
       ``timeout`` carries ``url`` and means an HTTP request outlived its deadline and was aborted, so a mutation may have committed.
       ``csrf`` means a deferred token could not be fetched, so the mutation never left.
       ``incomplete`` carries ``boundaries``, the ids of the boundaries a streamed partial response ended without filling.
       ``undelivered`` carries ``url`` and ``reason`` and means a mutation sent over the WebSocket may have run on the server but its reply never came back, so the page should re-read its state rather than resend.
       For the ``reason`` ``http_only`` the runtime reloads the page right after the event, and a listener runs before the reload but cannot cancel it.
       Each cause carries only its own fields, so a listener branches on ``kind`` before reading ``status`` or ``body``.
   * - ``partial:settled``
     - ``Settled``, ``{zones, boundaries, status}``, once a request finished and once a batch of stream chunks applied, naming the zones and the boundary ids it settled and the ``status`` of its ``Outcome``.
   * - ``partial:layer-opened``
     - ``{opener, root}``, the opening element, or ``null`` for a server-initiated open, and the zone container the body fills.
   * - ``partial:layer-accepted``
     - ``{result}``, the accept result the closing side passed, or ``undefined`` for a close without one.
   * - ``partial:layer-dismissed``
     - ``{reason}``, one of ``escape``, ``backdrop``, ``popstate``, ``navigate`` for a layer a router navigation closes, ``dismissed``, the ``returnValue`` of a ``<form method="dialog">`` close or ``dialog`` when it is empty, or the text a server dismiss carried.
   * - ``next:stream-state``
     - ``{url, transport, state, code?}``, on every change of a stream connection, see `Connection state events`_.
   * - ``next:stream-gap``
     - ``{topic, from, to}``, for every gap or epoch change that triggers a zone resync.
   * - ``next:toast``
     - ``{text, variant, element}``, fired once the toast item is in its tray, with ``element`` the inserted item.
   * - ``next:navigated``
     - ``{url, path, title, action}``, once per commit that changes the address or the title, and never for the page load.
       A change of the title alone carries ``action: "none"``, see :doc:`client-extras`.
   * - ``next:consent``
     - ``{granted, denied, changed, initial}``, on every consent choice and once for the state the page starts from.
   * - ``next:script-loaded``, ``next:script-error``
     - ``{name}`` and ``{name, url}``, for a script of the manifest.

:doc:`client-extras` describes the navigation, consent, and script events in the last four rows.
One rule covers the channels.
Every event of the table fires on the document as a ``CustomEvent`` and on the ``Next.on`` bus with the same payload, except ``ready`` and ``context-updated``, which reach the bus alone because the store is a runtime concern rather than a DOM one.
The rule holds for every source, so a ``partial:error`` from the wire, the asset loader, a stream, a chunk that failed to load, or a lazy CSRF fetch reaches both channels as the ``op`` kind of the apply pipeline does.

Document and element events
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The runtime also dispatches ``CustomEvent`` instances on the DOM, a channel distinct from the ``Next.on`` bus.
The element events bubble and are caught with a delegated ``document.addEventListener``, and the document events are dispatched on the document directly.
A cancelable event is the only kind whose ``preventDefault()`` changes what the runtime does, and a ``Next.on`` listener for the same name cannot veto anything.

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
   * - ``partial:before-request``
     - The document
     - No
     - The fetch layer, before the request leaves.
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
     - Every source of a failure, the applier, the fetch layer, the asset loader, a stream, the chunk loader, the lazy CSRF fetch, and the reader of a streamed partial response.
   * - ``partial:settled``
     - The document
     - No
     - The fetch layer once a request finished, and the boundary fills once a batch applied.
   * - ``partial:layer-opened``
     - The document
     - No
     - The layer stack, once the dialog and its zone container are in the document.
   * - ``partial:layer-accepted``
     - The document
     - No
     - The layer stack, on every close that is not a dismissal, with or without a result.
   * - ``partial:layer-dismissed``
     - The document
     - No
     - The layer stack, on a browser gesture or a server dismiss.

``next:navigated``, ``next:consent``, ``next:script-loaded``, and ``next:script-error`` fire on the document as well, as the channel rule of `Runtime events`_ states, see :doc:`client-extras`.

The ``event`` patch verb dispatches a server-named ``CustomEvent`` on the document and on the bus through the same path, so an application event name joins this channel without a client registration.
:doc:`/content/topics/partial-rendering/extending` covers the server side and the reserved names the builder refuses.
The mount and morph events run inside the apply, which is what lets a framework island veto the morph of its own root and manage that subtree itself, see :doc:`/content/topics/partial-rendering/framework-islands`.

The partial surface
~~~~~~~~~~~~~~~~~~~

``Next.partial`` is a ``PartialSurface``, one object built when the bundle evaluates and never replaced.
``layers`` and ``stream`` are getters over live sub-modules, so a held reference to the surface keeps reaching the current stack and registry.
``stream`` is the transport-neutral API over SSE and the WebSocket alike.

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
     - ``Promise<Outcome>``
     - Send one partial request through the queues and the per-uid lock, and resolve with its `Outcome`_ once the reply is applied.
       Network, HTTP, and parse failures surface as ``partial:error`` and as an outcome rather than as a rejection.
       A mutation sent while its uid is locked is not sent and resolves as ``dropped``.
       An HTTP request still unanswered after its deadline is aborted, fires ``partial:error`` of kind ``timeout`` with its ``url``, resolves as ``error``, and frees its lock.
       The default deadline is 30 seconds.
       A ``$timeout`` key of the init payload sets it in milliseconds for every HTTP request of the page, and ``0`` turns it off, while the framework writes no such key itself.
       A WebSocket tunnel has its own timeout, and the HTTP deadline starts after the tunnel step.
       ``navigate: false`` keeps the page on a redirect, or on a non-envelope reply to a GET, and resolves its URL in ``location`` instead of navigating there.
       A zone GET refused with 403 or 404 publishes ``partial:error`` of kind ``http`` with the status and the body before the runtime navigates to the page, and a redirect to the login page navigates without the event.
       The request goes to an absolute URL on the page's own origin with ``mode: "same-origin"``, and a URL off that origin is refused unsent with a ``partial:error`` of kind ``network``.
       ``transport`` is ``"http"`` or ``"any"``, and the triggers set it from the form's ``data-next-transport``.
       A POST marked ``"any"`` rides the open WebSocket when one advertises the tunnel, see `The stream API`_, and anything else, or a request the socket cannot carry, goes over ``fetch``.
   * - ``submit(target: HTMLFormElement | SubmitAction)``
     - ``Promise<Outcome>``
     - Send a form, or an action without one, through the same pipeline as a declarative submit and resolve with its `Outcome`_.
       A form posts to its ``action`` with its fields, zone, key, and ``data-next-transport``, locked on its ``data-next-action`` uid, or on its URL without one.
       A ``SubmitAction`` is ``{ action, data?, zone?, transport?, key?, navigate?, from? }``, where ``action`` is the URL ``{% action_url %}`` renders and ``data`` is a ``FormData`` or a record of strings, numbers, booleans, and blobs.
       The body gains ``_next_form_origin`` with the page of ``from``, or the current path and query without it, unless ``data`` already carries one, and ``transport`` defaults to ``"http"``.
       ``from`` is the element the action is fired from, and a ``from`` inside a modal layer stamps the owner page and the ``X-Next-Origin`` host the way a ``<form>`` in that layer does, so the reply resolves its zones against the right page.
       A submit while the lock is held waits for it, and the waiting submits run one by one in the order they were made, so three quick creates send three requests.
       A ``SubmitAction`` locks on its ``key``, or on its ``action`` without one, so moves of two cards keyed by card id neither wait for nor replace each other.
       With a ``key`` the newest waiting submit wins and the older one resolves as ``dropped``, so only the last move of a card is sent after the one in flight.
       The ``key`` also travels as the request key, so a ``form`` op of the reply prefers the repeated form whose ``data-next-key`` matches it.
       With ``navigate: false`` a redirect reply leaves the page in place and resolves ``ok`` with its target in ``location``, over ``fetch`` and the WebSocket tunnel alike.
   * - ``defineOp(name: string, handler: OpHandler)``
     - ``void``
     - Register the client handler of a custom verb.
   * - ``parseHook(contentType: string, hook: ParseHook)``
     - ``void``
     - Register a parser for a foreign response content type.
   * - ``setCsrf(csrf: CsrfPayload | undefined)``
     - ``void``
     - Replace the header and the token, or the header and the endpoint URL of a deferred token, that the next mutation submits with, and clear it with ``undefined``.
       A deferred token is fetched once before the first unsafe request, and an envelope carrying a rotated token overwrites whatever was set here.
       A token whose unmasked secret is new reconnects the open WebSockets once their pending replies settle, and a fresh masking of the same secret keeps them.
   * - ``onMount(selector: string, callback: (el: Element) => void)``
     - ``() => void``
     - Register a mount callback and receive the teardown that unregisters it.
       The callback runs over every matching element a patch inserts, and a registration made after the runtime is ready catches up over the present document at once.
   * - ``layers``
     - ``LayerStack``
     - The live stack of open layers, for driving a modal from script, see :doc:`/content/topics/partial-rendering/layers`.
   * - ``stream``
     - ``StreamSurface``
     - The stream registry and clock API.
       ``on(topic, name, callback)`` adds a topic listener, and ``meta()`` reads metadata during synchronous delivery.
       ``send(topic, name, data, options?)`` sends an upstream event over the WebSocket.
       ``now()`` and ``offset()`` estimate server time.
       ``scan``, ``remember``, ``forget``, ``size``, and ``_reset`` are the seams the apply pipeline and the harness drive, not application API.
       See `The stream API`_ and :doc:`/content/topics/realtime`.
   * - ``mount(nodes: readonly Element[])``
     - ``void``
     - Run the mount pass over elements inserted outside an envelope.
   * - ``ready()``
     - ``void``
     - Seed the asset registry from the document, run the mount callbacks over it, then arm the triggers.
   * - ``_current()``
     - ``NavigationState``
     - The current URL, path, and title, the value ``Next.navigation.current()`` returns, a bootstrap seam.
   * - ``_configure(adapters: PartialAdapters)``
     - ``void``
     - Swap the platform seams and rebuild the sub-modules, a harness seam.
   * - ``_reset()``
     - ``void``
     - Tear down every registry, timer, observer, and open layer, a harness seam.

WireRequest fields
~~~~~~~~~~~~~~~~~~

``fetch`` takes a ``WireRequest``, and only ``url`` is required.

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Field
     - Meaning
   * - ``url``
     - The target, an absolute URL or a path on the page's own origin.
   * - ``method``
     - The HTTP method.
   * - ``uid``
     - The form uid a mutation locks on.
       Without it a mutation runs unlocked.
   * - ``zone``
     - The value of ``X-Next-Zone``, absent when the answer addresses the whole page.
   * - ``queue``
     - The queue and abort identity of a safe GET, defaulting to the zone.
       A newer GET of the same queue aborts the older one.
   * - ``headers``
     - Extra request headers.
   * - ``body``
     - The request body, a ``BodyInit``.
   * - ``abortable``
     - Marks a POST that mutates nothing, such as an inline validation.
       It joins the abortable queue and skips the mutation lock.
   * - ``key``
     - The ``data-next-key`` of the initiating form, threaded to the apply for a repeated form.
   * - ``owner``
     - The page a mutation was submitted from, which receives the ``meta`` op of the reply.
   * - ``transport``
     - ``"any"`` lets a POST ride the open WebSocket, and ``"http"`` or absent keeps it on ``fetch``.
   * - ``wait``
     - What a mutation does while its uid is locked.
       Absent, it is dropped and resolves as ``dropped``.
       ``true`` queues it behind the lock, and ``"latest"`` queues it and drops the older waiters.
   * - ``navigate``
     - ``false`` keeps the page on a redirect and resolves its URL in ``location``.
   * - ``boundaries``
     - The boundary wrappers the request reloads, which the runtime marks busy while it runs and names in ``partial:settled``.

.. _ref-client-outcome:

Outcome
~~~~~~~

``fetch`` and ``submit`` resolve with an ``Outcome`` of ``status``, ``applied``, and the optional ``errors``, ``envelope``, ``error``, and ``location``.
The promise never rejects, and the usual ``partial:before-request``, ``partial:applied``, and ``partial:error`` events still fire.

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - ``status``
     - Meaning
   * - ``"ok"``
     - An envelope came back with a status below 400 and no invalid form, or a reply that is no envelope sent the runtime to another page.
   * - ``"invalid"``
     - The envelope's form meta is invalid, and ``errors`` holds its field errors.
   * - ``"error"``
     - The request fired ``partial:error``, kept in ``error``, or an envelope came back with a status of 400 or above, or the CSRF chunk failed to load.
   * - ``"dropped"``
     - The request was never answered for this caller, since the lock dropped it, a newer submit of the same ``key`` replaced it, or a newer GET of the same queue aborted it.

``applied`` is the ``ok`` of the matching ``partial:applied``, true once every op ran, and false when an op failed, the page refused the envelope in ``partial:before-apply``, a version mismatch reloaded the page, or no envelope came back.
A mount callback or a script that throws after the ops still settles the outcome with ``applied`` false and frees the lock, and the error itself propagates to the page.
A header name the browser refuses fires ``partial:error`` of kind ``network`` and sends nothing, and a ``SubmitAction`` whose ``data`` cannot be read resolves as ``error`` with that ``network`` error in ``error`` and fires nothing.
``envelope`` is the parsed envelope whenever one came back, and ``boundaries`` lists the ids of the boundaries a streamed reply filled.
``location`` is the absolute URL a reply would have navigated to, set only for a request sent with ``navigate: false``, the way a create returns the id of the new row without leaving the page.
A declarative submit still drops a second submit of a busy form, so a double click sends one request, while ``submit`` queues because a script submits on purpose, such as a second drop of a drag while the first is in flight.

.. code-block:: javascript
   :caption: static/board/move.js

   const outcome = await Next.partial.submit({
     action: board.actions.move,
     data: { card: id, column, position },
     zone: "board",
     transport: "any",
     key: `card-${id}`,
   });
   if (outcome.status !== "ok" || !outcome.applied) rollback();

   const created = await Next.partial.submit({
     action: board.actions.create,
     data: { title },
     navigate: false,
   });
   const card = new URL(created.location ?? location.href).searchParams.get("created");

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
A hook that throws, and a value the applier rejects as no envelope, surface as a ``partial:error`` of kind ``parse``.
A frame of a streamed response that is no JSON, met after the first fill landed, fires the same kind with the frame as its ``body``, and the stream goes on with the next frame.
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

.. _ref-client-stream:

The router
~~~~~~~~~~

``Next.router`` is the surface of the ``router`` bundle, present on a document the client router serves, see :doc:`/content/topics/router/index`.

.. list-table::
   :header-rows: 1
   :widths: 34 20 46

   * - Member
     - Returns
     - Description
   * - ``visit(url, {replace?, scroll?})``
     - ``Promise<void>``
     - Navigate to ``url`` as a click would, resolving once the navigation settled.
       ``replace`` replaces the history entry, ``scroll: false`` keeps the scroll position, and a URL the router does not take over loads hard.
   * - ``prefetch(url)``
     - ``Promise<void>``
     - Prefetch ``url`` into the cache a navigation applies, loading the ``history`` and ``intent`` bundles first when needed, whatever ``ROUTER["PREFETCH"]`` says, see :doc:`/content/topics/router/prefetch`.
   * - ``flush()``
     - ``void``
     - Drop every history snapshot and every prefetched answer.
   * - ``configure({focus?, announce?, host?})``
     - ``void``
     - Replace the focus move after a navigation, ``focus(nodes)`` returning whether it moved focus, the announcement of the new title, ``announce(text)``, or the browser adapter, ``host``, a test seam.
   * - ``_install(factory)``
     - ``void``
     - The handshake the ``history`` and ``intent`` bundles call as they evaluate, not an application hook.

Every router event fires on the document as a ``CustomEvent`` and on the ``Next.on`` bus with the same payload, and only the document event of ``router:before`` can cancel.

.. list-table::
   :header-rows: 1
   :widths: 24 30 46

   * - Event
     - Detail
     - Fires
   * - ``router:before``
     - ``{url, trigger, cached}``
     - Before a navigation, cancelable, and never for a Back or Forward step, ``cached`` telling whether a prefetched answer is kept or a press request is in flight.
   * - ``router:start``
     - ``{url}``
     - As the navigation request is about to leave.
   * - ``router:before-swap``
     - ``{url, layouts, hold, nodes}``
     - Before the envelope applies, ``hold()`` returning a release function the swap waits for.
   * - ``router:commit``
     - ``{url}``
     - After the new content, the address, and the title are in place.
   * - ``router:end``
     - ``{url, status, initial?}``
     - Once the navigation settled, ``status`` being ``complete``, ``hard``, or ``superseded``, and once at boot with ``initial: true``.
   * - ``router:error``
     - ``{url, error}``
     - When the request throws or the envelope fails to apply, before the hard load.

Router attributes
^^^^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 30 22 48

   * - Attribute
     - Author
     - Meaning
   * - ``data-next-router``
     - Hand-written
     - ``off`` keeps the links and forms inside it out of the router, and under ``ROUTER["SCOPE"] = "opt-in"`` the attribute with any other value takes the links inside it in.
   * - ``data-next-scroll="preserve"``
     - Hand-written
     - A link inside it keeps the scroll position.
   * - ``data-next-scroll-region``
     - Hand-written
     - An element whose own scroll Back and Forward restore beside the scroll of the window.
   * - ``data-next-focus``
     - Hand-written
     - The element that takes focus after a navigation, ahead of the first ``<h1>``.
   * - ``data-next-persist``
     - Hand-written
     - An element a navigation moves into the place of the element with the same value in the new content, so its state survives the swap.
   * - ``data-next-prefetch``
     - Hand-written
     - Overrides ``ROUTER["PREFETCH"]`` for the links inside it, ``viewport`` prefetching them as they scroll into view, ``intent`` only on intent, and ``off`` never, which also keeps them out of the Speculation Rules.
   * - ``data-next-press``
     - Hand-written
     - Overrides ``ROUTER["PRESS"]`` for the links inside it, ``mouse`` sending the full page request at the press of a mouse, ``all`` at the press of every pointer, and ``off`` keeping every press at the shell depth.
   * - ``data-next-transition``
     - The runtime
     - Set on ``<html>`` as each view transition under ``ROUTER["TRANSITIONS"]`` starts, ``forward``, ``back``, or ``replace``.
   * - ``data-next-asset="page"``
     - The server
     - On each stylesheet a routed document injects, which the router disables once a page that does not list it is shown.
   * - ``data-next-pending``
     - The runtime
     - On the link or form that started a navigation, until it commits or ends.
   * - ``data-next-navigating``
     - The runtime
     - On ``<html>`` once a navigation outlasts ``ROUTER["PROGRESS_DELAY"]``, beside ``aria-busy="true"`` on the element around the slot that will change.
       The default progress bar of ``ROUTER["PROGRESS"]`` is drawn from it.
   * - ``aria-current``, ``data-next-current``
     - The server, then the ``intent`` bundle
     - The active-link marks, ``page`` or ``ancestor``, on the links of a navigation container, and ``data-next-current="off"`` hand-written to keep a link or a container as written.
   * - ``data-next-match``
     - Hand-written
     - ``exact``, ``query``, or both, changing the active-link rule for a link or a container.
   * - ``data-next-nav``
     - Hand-written
     - Marks a navigation container whose links are marked, one of the default containers.

See :doc:`/content/topics/router/browser`, :doc:`/content/topics/router/prefetch`, and :doc:`/content/topics/router/active-links`.

The stream API
~~~~~~~~~~~~~~

``Next.partial.stream`` keeps one connection per distinct ``data-next-stream`` URL and an ``(epoch, seq)`` cursor per topic on it.
The cursor keeps the topics the last ``hello`` granted, at most 64, the limit a subscribe frame may carry.
Past 64 topics it drops the topic whose cursor advanced longest ago.
The sections below cover the listener API, the connection state events, the WebSocket negotiation, and the request tunnel.

Listening and sending
^^^^^^^^^^^^^^^^^^^^^

``on(topic, name, callback)`` calls ``callback(detail, meta)`` for each ``event`` patch of that name on that topic and returns the function that removes the listener.
``meta`` is a ``StreamMeta`` of ``topic``, ``epoch``, ``seq``, ``serverTime``, and ``connection``, and ``meta()`` returns the same object while a delivery runs and ``undefined`` outside it.
``connection`` is the opaque id the ``hello`` of the WebSocket carrying the topic names, the same id the server hands an upstream handler, so a listener can skip the echo of its own ``send``, and it is ``undefined`` on SSE.
``now()`` adds the median ``offset()`` of the last five ``server_time`` samples to the local clock.

``send(topic, name, data, options?)`` sends an upstream event over the WebSocket that carries ``topic`` and returns a ``Promise<boolean>``.
``options`` is ``{ ack?: boolean }``, and ``ack`` defaults to ``true``.
An ``up`` frame whose compact JSON, with its ``type``, ``topic``, ``name``, ``data``, and id, exceeds in UTF-8 bytes the ``upstream.frame`` limit of the socket's ``hello``, 16 KiB when the hello names none, resolves to ``false`` and is never sent.
Without options the frame carries an id and the promise resolves to ``true`` once the server acknowledges that a handler ran, and to ``false`` for a refusal, a missing acknowledgment, a dropped socket, or a topic no open socket carries.
The client waits for the acknowledgment five seconds past the ``upstream.timeout`` of the socket's ``hello``, and five seconds when the hello names none.
A ``timeout`` acknowledgment means the handler outlived the server's limit and may still have run, so it resolves to ``false`` and the event is not resent.
``{ ack: false }`` sends the frame without an id and resolves to ``true`` as soon as it is queued.
On an SSE connection there is no upstream channel, so the promise resolves to ``false`` and nothing is sent.
See :ref:`topics-realtime` for handlers.

Connection state events
^^^^^^^^^^^^^^^^^^^^^^^

Every change of a connection's state fires ``next:stream-state`` with ``url``, ``transport``, and ``state``.
``transport`` is ``"sse"`` or ``"ws"``, the names ``REALTIME['TRANSPORTS']``, the ``hello`` frame, and the ``stream_subscribed`` signal use, and from ``open`` on it names the transport actually carrying the connection, ``"sse"`` after a fallback.
The first ``connecting`` of a connection names the configured transport.
A connection is ``connecting`` while it opens and again while a dropped socket or ``EventSource`` waits to reconnect, ``open`` once the transport is up, and ``closed`` when it is paused, removed, or ended.
A page loaded in a hidden tab starts paused and opens its connections on the first show.
A paused or removed WebSocket reports ``closed`` at once but stays open until the tunneled requests and acknowledged ``up`` frames sent on it before then get their replies, each within its own timeout.
While it drains, it answers the server ping and ignores every other frame except those replies.
A ``next-end`` frame, sent when the topic authorizer denies a grant, is terminal, so the connection closes for good, its ``next:stream-state`` detail also carries the frame's ``code``, and ``partial:error`` of kind ``network`` fires with that code as its ``error``.
Only an SSE stream carries ``next-end``.
A WebSocket has no end frame, and a denied grant there closes the socket with ``4403`` after an ``error`` frame, which sends that stream URL to SSE for the life of the page, where the authorizer answers with ``next-end``.
A ``next:stream-gap`` event with ``topic``, ``from``, and ``to`` fires for every gap or epoch change that triggers a zone resync.
A change between two non-empty epochs resets the cursor and resyncs, while a change from the empty epoch of a never-published topic is contiguous at ``seq`` ``1`` and a gap above it.
A topic idle past the replay TTL starts a new epoch at ``seq`` ``1``, and a connection whose ``hello`` already landed and that has no resync in flight adopts that epoch without a resync, since the broker delivered every message live.
Any other epoch change, a later ``seq``, a pending resync, or a reconnect ``hello`` under a new epoch, still resyncs.

WebSocket negotiation
^^^^^^^^^^^^^^^^^^^^^

With WebSockets enabled, the client first sends the stream URL a GET with ``X-Next-Transport: ws`` and ``Accept: application/json``.
It opens the socket only for a ``200`` reply whose content type is ``application/json``.
A ``401``, ``403``, or ``406`` reply, any other content type, no reply within five seconds, or three failed attempts in a row sends that connection to SSE until a resume reopens it, and a ``4403`` close sends its stream URL to SSE for the life of the page.
A grant whose ``protocol`` is not ``2``, whose ``ticket`` is empty, or whose ``url`` leaves the page origin sends the connection to SSE the same way.

The server sends an ``error`` frame before a ``4400``, ``4401``, ``4403``, or ``4429`` close, and the client decides by the close code, while an ``error`` frame of code ``auth`` that no close follows within five seconds counts as a ``4401``.
A ``4401`` close drops the connection once and negotiates again for a fresh ticket, since two negotiations on a first visit can issue different anonymous identities and the browser keeps the cookie of the last.
A second ``4401`` with no ``hello`` between the two sends the connection to SSE until a resume reopens it, and each ``hello`` earns one such retry.
The retry does not count as a failed attempt.
Any other close, such as ``1013`` when the server's broker subscription ends, ``1011``, ``4400``, or ``4429``, counts as a failed attempt and reconnects with backoff.

The failure count resets only after a socket stayed open for one heartbeat past its ``hello``, so a server that drops every socket right after ``hello`` still ends on SSE.
A socket that sends no ``hello`` within five seconds of opening is closed as a transient failure.
The ``hello`` frame names the server's ``heartbeat`` in seconds, and a socket that receives no frame for two and a half heartbeats counts as dropped, is closed, and reconnects.
A ``patches`` frame whose ``data`` is a string hands that text to the registered parse hook unchanged, the same text an ``EventSource`` would deliver.
A ``patches`` frame whose ``data`` is an object is applied as is and skips the hook.

The request tunnel
^^^^^^^^^^^^^^^^^^

A socket tunnels requests only once its ``hello`` carries the ``tunnel`` key, and without it every request of that socket goes over ``fetch``.
``tunnel.timeout`` is the server's request timeout in seconds, and the client waits five seconds past it, 35 seconds when it is missing.
``tunnel.frame`` caps a whole frame in bytes, 64 KiB when it is missing.
``tunnel.headers`` and ``tunnel.prefixes`` list the request header names the tunnel accepts, and ``tunnel.csrf`` names the CSRF header, while a request carrying any other header goes over ``fetch``.
A body travels only as a string, ``URLSearchParams``, or ``FormData`` without files, and a body with a file goes over ``fetch``.

A refusal the server marks ``retry`` never reached the application, so the client resends the request over ``fetch`` once.
Any other refusal ends a mutation in ``partial:error`` of kind ``undelivered`` whose ``reason`` is the refusal code.
When the echo of such a mutation already arrived and the ring dropped it, the stream resyncs the zones of its page once, and an echo that arrives later applies as a foreign event.
The codes with a rule of their own are in the table.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Reason
     - What the client does
   * - ``disabled``
     - Resends over ``fetch`` and stops the tunnel on that socket.
   * - ``stale``
     - Resends over ``fetch`` and reconnects the socket once for fresh cookies.
       A second ``stale`` on the renewed socket stops the tunnel on it.
   * - ``http_only``
     - Ends the mutation in ``undelivered``, and after the event the runtime reloads the page.
       The server marks a response it keeps to plain HTTP this way, such as one that sets a cookie or rotates the session, so the page shows the server state under the session the browser still holds.
   * - ``closed``
     - Raised by the client when the socket dropped before the reply came.
   * - ``timeout``
     - Raised by the client when no reply came in time.
   * - any other code
     - Ends the mutation in ``undelivered`` unless the server marked it ``retry``.

An inline validation mutates nothing, so in each of these cases it resends over ``fetch`` instead.

The attribute contract
~~~~~~~~~~~~~~~~~~~~~~

The runtime reads one attribute namespace, ``data-next-*``, and thirty-three names make up the contract of zones, forms, pagination, layers, and streams.
The attributes of deferred boundaries follow in `Boundary attributes`_.
They fall into authorship groups, and a reader needs the split to know which are theirs to write.
The server writes fifteen of them alone, seven from ``{% zone %}`` and ``{% form %}``, six from ``{% more %}``, one from ``{% #consented %}``, and one on every script tag it renders from ``scripts.py``.
The runtime writes six as styling, state, and bookkeeping hooks, and six are hand-authored on plain markup.
The remaining six carry both authors, written by a ``{% form %}`` parameter, by ``{% more %}``, or on a boundary wrapper, and by hand on the element that drives the same behaviour without one.
``{% form %}`` reserves the ``data-next-`` prefix and raises a template syntax error on a hand-written one, so a hand-authored attribute goes on a wrapper, a link, or a submit control rather than on the tag.

.. list-table::
   :header-rows: 1
   :widths: 24 22 54

   * - Attribute
     - Author
     - Read by
   * - ``data-next-zone``
     - ``{% zone %}``
     - The applier resolves a ``zone`` target through it, the layer stack scopes that lookup per open layer, and the triggers require it on a polling element.
   * - ``data-next-defer``
     - ``{% more %}`` on its link, a client-trip boundary on its wrapper, hand-written on a pagination sentinel
     - On a ``{% more %}`` link or another pagination sentinel the value is not read, and the attribute only marks the link the triggers hand to the intersection observer.
       On a boundary wrapper the ``defer`` bundle reads it, fetching a ``load`` boundary on ready and a ``visible`` one once it scrolls into view, and leaving a ``manual`` one to its controls.
   * - ``data-next-poll``
     - ``{% zone poll= %}``
     - The triggers, which group every zone on one interval into a single timer chain and one batched GET.
   * - ``data-next-reload``
     - ``{% zone reload="loading" %}``
     - The reload bundle, which shows the loading content of the zone once a request that reloads it outlasts the delay.
       A ``data-next-reload`` outside a zone and a boundary wrapper also keeps the links inside it out of the client router.
   * - ``data-next-delay``
     - ``{% zone delay= %}``, or hand-written on a ``data-next-layer`` link
     - The reload bundle, which waits that many milliseconds, 150 when it is absent, before it shows the loading content of the zone or of the layer the link opens.
   * - ``data-next-action``
     - ``{% form %}``
     - The triggers intercept a submit and lock on the uid, and the applier resolves a ``form`` target by it.
       The triggers listen in the bubble phase, so a submit a handler of the form cancelled stays cancelled, and one that a handler stops from propagating posts natively.
       A submitter's ``formaction`` replaces the URL, and a ``formmethod`` other than ``post`` or a ``formtarget`` or form ``target`` naming another browsing context leaves the submit to the browser.
   * - ``data-next-transport``
     - ``{% form %}``, from the transport of the action
     - The triggers, which let the submit and the inline validation of the form ride the open WebSocket when the action is declared ``transport="any"``, see :ref:`topics-forms-actions-transport`.
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
     - ``{% more merge= %}``, hand-written on a pagination link or sentinel
     - The triggers, which GET the link with an ``append`` or ``prepend`` intent so the server authors the merge patch.
       A click with a modifier key or a button other than the primary one, a click a handler cancelled, and a link with ``download`` or a ``target`` other than ``_self`` stay with the browser.
   * - ``data-next-margin``
     - ``{% more margin= %}``
     - The triggers, which pass it to the intersection observer of an infinite-scroll sentinel as its root margin.
   * - ``data-next-param``
     - ``{% more param= %}``
     - The ``more`` bundle, which reads the token of a fetched batch from that query parameter, ``page`` when it is absent.
   * - ``data-next-prune``
     - ``{% more prune= %}``
     - The ``more`` bundle, which drops whole batches from the top of the list once it holds more rows than that.
   * - ``data-next-back``
     - ``{% more %}`` on a link with ``prune=`` or ``url=``, from the ``back`` of a ``Window``
     - The ``more`` bundle, which keeps it with the rows the link fetches as the token of the batch before them, and writes it into the ``<zone>-less`` link once a prune makes those rows the top of the list.
   * - ``data-next-backs``
     - ``{% more %}`` on the link of a restored window, from the ``backs`` of a ``Window``
     - The ``more`` bundle, which splits the rows of the restored window evenly across its batches and keeps one space-separated token with each batch after the first.
   * - ``data-next-url``
     - ``{% more url= %}``
     - The ``more`` bundle, which writes ``?<param>=<token>&restore=1`` into the address bar after each fetched batch, with ``replace`` or ``push``.
       The write is a ``url`` operation with ``quiet: true``, so it does not count as a page view.
   * - ``data-next-batch``
     - The runtime
     - The ``more`` bundle stamps each row of a list whose link sets ``prune=`` or ``url=`` with the batch it arrived in and reads it back to drop whole batches.
   * - ``data-next-confirm``
     - Hand-written on a link or a submit control
     - The triggers, which run the confirm gate on the nearest ancestor carrying it and cancel the interaction on a refusal.
   * - ``data-next-keep``
     - Hand-written on any element
     - The morph engine, which leaves the node and its subtree untouched so a mounted island root survives.
   * - ``data-next-reserve``
     - Hand-written on an element around a zone or a boundary
     - The reload bundle, which holds the height of a zone or a boundary while it shows loading content, unless an element around it carries ``data-next-reserve="off"``.
   * - ``data-next-stream``
     - Hand-written on a container
     - The stream bridge, which opens one stream per distinct URL, over WebSocket when negotiation succeeds and over ``EventSource`` otherwise, and rescans each inserted subtree.
   * - ``data-next-layer``
     - Hand-written on an ``<a>``
     - The layer stack, which opens the href in a modal and names the zone the dialog hosts.
       The client router prefetches the zone under the intent, viewport, and press rules, and the layer takes that answer instead of sending its own request.
       A link missing either the zone or the href stays a plain navigation.
       So does a click the pagination rule of ``data-next-merge`` leaves to the browser, such as a Ctrl-click that opens the URL in a new tab.
   * - ``data-next-accepted``
     - Hand-written on the opening ``<a>``
     - The layer stack, which re-fetches the named zone of the host page once the layer closes by accept, with or without a result.
   * - ``data-next-consented``
     - ``{% #consented %}``
     - The scripts chunk, which swaps the inert ``<template>`` carrying it for its body once the named category is granted.
   * - ``data-next-script``
     - The server, on every script tag of ``scripts.py``
     - The scripts chunk, which finds a script tag the server already rendered by it so the tag is not inserted twice and ``load()`` resolves on it.
   * - ``data-next-busy``
     - The runtime
     - The runtime writes it for the span of a request on every zone the request reloads, on every boundary wrapper it fetches, and on the opener of a layer, alongside ``aria-busy="true"`` except for a background poll.
       The layer stack reads it back as the double-click guard, and the morph engine leaves both attributes alone while the element is busy.
   * - ``data-next-dialog``
     - The runtime
     - Nothing in the runtime reads it.
       It marks every layer ``<dialog>`` as the styling hook of the modal shell.
   * - ``data-next-toasts``
     - Hand-written on the page's own tray, or the runtime on the ``<div aria-live="polite">`` it appends to the body
     - The layer stack, which puts every toast into the first tray it finds top layer down and builds its own only when the page renders none.
   * - ``data-next-toast``
     - The runtime
     - Nothing in the runtime reads it.
       It marks one toast item and its value is the variant.
   * - ``data-next-run``
     - The runtime
     - The router marks the ``<template>`` that holds the place of each inline script carrying the page nonce in the HTML of a navigation, and replaces it with a fresh script once the swap commits, so the script runs.

:doc:`/content/topics/partial-rendering/reference` records the accepted values, the closed value sets, and the dev warnings each attribute earns.

Boundary attributes
^^^^^^^^^^^^^^^^^^^

The server writes every attribute of a boundary wrapper, and the runtime keeps them current as the boundary settles.
A page reads ``data-next-state`` and ``data-next-error`` from a stylesheet, and writes ``data-next-fetch`` and ``data-next-retry`` into its loading and error UI.

.. list-table::
   :header-rows: 1
   :widths: 26 22 52

   * - Attribute
     - Author
     - Meaning
   * - ``data-next-boundary``
     - The server
     - The boundary id, equal to the ``id`` of the wrapper, which a chunk and a reload address.
   * - ``data-next-state``
     - The server, then the runtime
     - ``loading`` while a stream job runs, ``idle`` while a client-trip boundary waits, ``ready``, or ``error``.
   * - ``data-next-error``
     - The server, then the runtime
     - The ``ErrorKind`` of an ``error`` state, ``incomplete`` for a boundary a stream ended without filling, and ``timeout`` or ``network`` for a fetch the browser gave up on.
   * - ``data-next-error-digest``, ``data-next-boundary-source``, ``data-next-error-detail``
     - The server, under ``DEBUG``
     - The digest of the failure, the ``error.djx`` that rendered, and the traceback where no ``<details>`` element may stand.
   * - ``data-next-boundary-key``
     - The server
     - The key of the call site, the same across responses, which a zone reload matches a stashed loading UI by.
   * - ``data-next-token``, ``data-next-transport``
     - The server, on a wrapper that can be fetched
     - The boundary token, and the place the token travels in when the transport is not the header.
   * - ``data-next-defer``
     - The server, on a client-trip wrapper
     - ``load``, ``visible``, or ``manual``, read by the ``defer`` bundle.
   * - ``data-next-adopted``
     - The server
     - Marks a wrapper whose attributes sit on the root element of its body, so a fetched body keeps them.
   * - ``data-next-fetch``
     - Hand-written in a loading UI
     - A click fetches the client-trip boundary around the control.
   * - ``data-next-retry``
     - Hand-written in an error UI
     - A click fetches the boundary again when it carries a token, reloads the nearest zone otherwise, and reloads the page outside every zone.
   * - ``data-next-streamed``
     - The server, on the root element
     - Marks a streamed page, so ``load`` boundaries fetch without waiting for the whole document.
   * - ``data-next-loading``
     - The server, on a ``<template>``
     - Holds the loading UI a ``reload="loading"`` zone shows, named after the zone.
   * - ``data-next-layer``
     - The server, on a ``<template>``
     - Holds the loading UI a layer of that zone shows until its answer arrives, written before the first link that opens it.

See :doc:`/content/topics/boundaries/streaming` for the states and :doc:`/content/topics/boundaries/errors` for the error UI.
:doc:`template-tags` records the ``{% zone %}``, ``{% form %}``, and ``{% #consented %}`` parameters that compile to the eleven names a template tag can write, and :doc:`/content/topics/scripts/declaring` covers the script tags that carry ``data-next-script``.

Exported types
~~~~~~~~~~~~~~

The entry module exports twenty-one type names.
See :doc:`client-extras` and :doc:`/content/topics/realtime` for the navigation, consent, scripts, and stream types.
A page written in TypeScript reaches them through the bundle's declaration output rather than by importing ``next/client/``.

.. code-block:: typescript
   :caption: the exported surface of next/client/next.ts

   export type NextContext = Readonly<Record<string, unknown>>;

   export interface NextChunks {
     scripts: ScriptsChunk;
   }

   export interface NextEventMap {
     ready: NextContext;
     "context-updated": { context: NextContext; changed: string[] };
     "partial:before-request": {
       url: string;
       method: string;
       intent: { zone?: string; uid?: string };
     };
     "partial:before-apply": BeforeApply;
     "partial:applied": {
       envelope: Envelope;
       ok: boolean;
       nodes: readonly Element[];
       stale?: true;
     };
     "partial:error": PartialError;
     "partial:settled": Settled;
     "partial:layer-opened": { opener: HTMLElement | null; root: HTMLElement };
     "partial:layer-accepted": { result: unknown };
     "partial:layer-dismissed": { reason: string };
     "next:stream-gap": { topic: string; from: number; to: number };
     "next:stream-state": {
       url: string;
       transport: "sse" | "ws";
       state: "connecting" | "open" | "closed";
       code?: string;
     };
     "next:toast": { text: string; variant: string; element: Element };
     "next:navigated": NavigatedDetail;
     "next:consent": ConsentChange;
     "next:script-loaded": { name: string };
     "next:script-error": { name: string; url: string };
   }

   export type { PartialError, PartialErrorKind } from "./protocol";
   export type { NavigatedDetail, NavigationAction, NavigationState } from "./navigation";
   export type { Asset, AssetLoad, BeforeApply, Envelope, FormMeta, Patch } from "./apply";
   export type { ConsentChange } from "./consent";
   export type { ScriptsChunk } from "./chunks";
   export type { ScriptStatus } from "./scripts";
   export type { StreamMeta } from "./sse";
   export type { Outcome, Settled } from "./wire";
   export type { SubmitAction } from "./partial";

``PartialError`` is the discriminated union every ``partial:error`` payload inhabits, over the ``network``, ``timeout``, ``http``, ``parse``, ``op``, ``asset``, ``csrf``, ``undelivered``, and ``incomplete`` kinds, each with an optional ``zones`` list.
``BeforeApply`` is the payload of ``partial:before-apply`` and ``Settled`` the payload of ``partial:settled``.
``PartialErrorKind`` is its discriminant, aliased for a listener that switches on it.
``PartialSurface`` is declared in the partial module and not re-exported, so its name reaches a reader through this page rather than through an import.

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
     - The delegated ``data-next-*`` handlers, the ``data-next-defer`` triggers, the fetch of the poll chunk, and inline validation.
   * - ``sse.ts``
     - The ``EventSource`` transport and the factory that builds the SSE side of a stream.
   * - ``stream.ts``
     - The transport-neutral stream state, the ``(epoch, seq)`` cursors, the zone resync, the topic listeners and their metadata, the echo ring of own request ids, and the pause and resume on tab visibility.
   * - ``ws.ts``
     - The WebSocket transport, its negotiation and ticket, the upstream frames, and the request tunnel, shipped in the ws chunk.
   * - ``diagnostics.ts``
     - The development diagnostics of a ``DEBUG`` render, shipped in the dev chunk.
   * - ``assets.ts``
     - The asset loader, the registry of URLs already on the page, and the asset version safeguard.
   * - ``dirty.ts``
     - The touched-element tracker whose snapshots keep a response from overwriting what the user typed.
   * - ``adapters.ts``
     - Default platform adapters over the browser globals a test harness replaces.
   * - ``events.ts``
     - The plain-activation test of a click and the in-place test of a browsing-context name, shared by the triggers, the layer openers, the router, and the intent bundle, and the temporary ``tabindex`` that lets an element take the focus a deferred boundary or a navigation moves to it.
   * - ``protocol.ts``
     - The wire vocabulary shared with the server, the content type, the request and router headers, the ``PartialError`` union, and ``pageKey``, the same-origin path and query a layer and a zone are keyed by.
   * - ``navigation.ts``
     - The history writes of a commit and the one ``next:navigated`` it fires, a layer's push written as it opens and announced once its body commits.
   * - ``head.ts``
     - The four URL-bound head tags a ``meta`` operation syncs and a layer snapshots and restores.
   * - ``csrf.ts``
     - The token store, the single-flight fetch of a deferred token, and the prefetch on the first focus in a form.
       The fetch itself ships in the csrf chunk.
   * - ``chunks.ts``
     - The core side of the lazy chunks, which fetches each on demand, and of the scripts chunk, which answers ``Next.ready("scripts")`` once it loads.
   * - ``consent.ts``, ``scripts.ts``
     - Consent and the script manifest loader, shipped in the scripts chunk.
   * - ``poll.ts``
     - The zone poller, its interval groups, and the pause and resume on tab visibility, shipped in the poll chunk.
   * - ``drain.ts``
     - The queue that applies the fills of a streamed page or partial response and marks a boundary left ``loading`` as ``incomplete``.
   * - ``defer.ts``
     - The client-trip fetch, the retry and fetch controls, and the page reload a refused expired token asks for, shipped in the defer bundle.
   * - ``reload.ts``
     - The loading UI of a reloading zone and its boundary skeletons, shipped in the reload bundle.
   * - ``more.ts``
     - The pruning and the address writes of an infinite list, shipped in the more bundle.
   * - ``reader.ts``
     - The reader of a streamed response, shipped in the reader bundle.
   * - ``layout.ts``
     - The slot markers of the layout chain and the bounds a navigation target resolves to, shared by the router and the history bundle.
   * - ``router.ts``
     - The client router, its link and form takeover, the navigation request, the commit, the progress marks, and the loading of the history and intent bundles, shipped in the router bundle.
   * - ``history.ts``
     - The history snapshots, the scroll restore, the scroll after a push, the memo of URLs that answered no envelope, and the memo of shared pages fetched as plain documents, shipped in the history bundle.
   * - ``swap.ts``
     - The focus move, the announcement, persisted elements, view transitions, page stylesheet retirement, the inline scripts a navigation runs, and the reading of a foreign ``text/html`` answer, shipped in the history bundle.
   * - ``prefetch.ts``
     - The prefetch queue of the viewport and intent prefetch, the cache of prefetched answers, the answer a layer takes, and the stylesheets a landed answer fetches ahead, shipped in the intent bundle.
   * - ``current.ts``
     - The client pass of active links, shipped in the intent bundle.
   * - ``submit.ts``
     - The URL a ``GET`` form navigates to, built from its fields and its submit button, shipped in the history bundle.
   * - ``entries/scripts.ts``, ``entries/stream.ts``, ``entries/ws.ts``, ``entries/csrf.ts``, ``entries/poll.ts``, ``entries/dev.ts``
     - The entry of each lazy chunk, one module per chunk named after it.
   * - ``entries/defer.ts``, ``entries/reload.ts``, ``entries/more.ts``, ``entries/reader.ts``, ``entries/router.ts``, ``entries/history.ts``, ``entries/intent.ts``
     - The entry of each declared bundle.

Configuration
-------------

Nothing configures the runtime from the browser side.
``NEXT_JS_OPTIONS`` inside ``NEXT_FRAMEWORK`` decides how the bundle and the inline ``_init`` payload reach the page, over the ``auto``, ``manual``, and ``disabled`` injection policies and the four tag templates.
The ``defer``, ``reload``, ``more``, ``reader``, and ``router`` bundles a page needs are declared by script tags right after the runtime, rendered through ``script_tag_template``, at the top of ``{% collect_scripts %}`` under ``manual`` and nowhere under ``disabled``.
``JS_CONTEXT_SERIALIZER`` decides how a context value is encoded on its way into ``Next.context``.
See :doc:`settings` for both keys and :doc:`/content/topics/static-assets/js-context` for the full option table.

The dev channel follows Django ``DEBUG`` rather than a client switch, see :ref:`topics-partial-rendering-reference`.

See also
--------

.. seealso::

   :doc:`/content/topics/partial-rendering/index` for the topic subtree the narrative lives in.
   :doc:`/content/topics/partial-rendering/reference` for the envelope fields, the patch verbs, and the headers.
   :doc:`/content/topics/partial-rendering/co-located-js` for the idioms a page script follows to survive a patch.
   :doc:`partial` for the server side that authors every envelope the runtime applies.
   :doc:`/content/contributing/quality-gates` for how the bundle is built and what holds its size.
