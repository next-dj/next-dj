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

A chunk that fails to load, or has not loaded within 15 seconds, fires ``partial:error`` of kind ``asset`` once and is fetched again on its next need, three attempts in all, so a stalled request holds no ``Next.ready``, form submit, or stream for good.
A chunk that lands after its timeout is still delivered to every subscriber that waited for it.
The TypeScript sources are not part of the installed package, so a project imports no TypeScript and installs no Node toolchain.
:doc:`/content/contributing/quality-gates` describes how the bundles are built and budgeted, and :doc:`static` describes the finder that serves them.

API tiers
---------

The surface splits into tiers that describe the intended audience for each name.
A member whose name starts with an underscore is a bootstrap or test seam rather than application API.

Stable.
   ``Next.context``, ``Next.on``, ``Next.use``, ``Next.ready``, ``Next.navigation``, ``Next.consent``, ``Next.scripts``, ``Next.partial.onMount``, ``Next.partial.mount``, ``Next.partial.apply``, ``Next.partial.fetch``, ``Next.partial.submit``, ``Next.partial.setCsrf``, ``Next.partial.layers``, and ``Next.partial.stream``.
   The ``next:*`` events and the ``partial:*`` document events belong to the same tier.
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
     - ``{envelope}``, the parsed envelope with a mutable op list.
       The veto lives on the document event of the same name, not here.
   * - ``partial:applied``
     - ``{envelope, ok, nodes}``.
       ``ok`` is ``false`` when any op threw or named an unknown verb, so a listener tells a clean apply from a degraded one that still mounted what did change.
       ``nodes`` are the elements the ops inserted or morphed, the roots the mount pass ran over.
   * - ``partial:error``
     - A ``PartialError``, discriminated on ``kind`` over ``network``, ``http``, ``parse``, ``op``, ``asset``, ``csrf``, and ``undelivered``.
       ``csrf`` means a deferred token could not be fetched, so the mutation never left.
       ``undelivered`` carries ``url`` and ``reason`` and means a mutation sent over the WebSocket reached the server but its reply never came back, so the action may have run and the page should re-read its state rather than resend.
       For the ``reason`` ``http_only`` the runtime reloads the page right after the event, and a listener runs before the reload but cannot cancel it.
       Each cause carries only its own fields, so a listener branches on ``kind`` before reading ``status`` or ``body``.
   * - ``partial:layer-opened``
     - ``{opener}``, the opening element, or ``null`` for a server-initiated open.
   * - ``partial:layer-accepted``
     - ``{result}``, the accept result the closing side passed.
   * - ``partial:layer-dismissed``
     - ``{reason}``, one of ``escape``, ``backdrop``, ``dialog``, ``popstate``, ``dismissed``, or the text a server dismiss carried.
   * - ``next:stream-state``
     - ``{url, transport, state, code?}``, on every change of a stream connection, see `Connection state events`_.
   * - ``next:stream-gap``
     - ``{topic, from, to}``, for every gap or epoch change that triggers a zone resync.
   * - ``next:toast``
     - ``{text, variant}``, fired alongside building the toast element.
   * - ``next:navigated``
     - ``{url, path, title, action}``, once per commit that changes the address or the title, and never for the page load.
       A change of the title alone carries ``action: "none"``, see :doc:`client-extras`.
   * - ``next:consent``
     - ``{granted, denied, changed, initial}``, on every consent choice and once for the state the page starts from.
   * - ``next:script-loaded``, ``next:script-error``
     - ``{name}`` and ``{name, url}``, for a script of the manifest.

:doc:`client-extras` describes the last four of the consent and script events.
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
   * - ``next:stream-state``
     - The document
     - No
     - The stream bridge, on every change of a connection state.
   * - ``next:stream-gap``
     - The document
     - No
     - The stream bridge, when a gap or epoch change triggers a zone resync.
   * - ``next:toast``
     - The document
     - No
     - The layer stack, alongside appending the toast element.
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
     - Every source of a failure, the applier, the fetch layer, the asset loader, a stream, the chunk loader, and the lazy CSRF fetch.
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
``envelope`` is the parsed envelope whenever one came back.
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
A paused WebSocket reports ``closed`` at once but stays open until the tunneled requests and acknowledged ``up`` frames sent on it before the pause get their replies, each within its own timeout.
While it drains, it ignores every frame except those replies.
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
The server sends an ``error`` frame before a ``4400``, ``4401``, or ``4403`` close, and the client decides by the close code, while an ``auth`` frame that no close follows within five seconds counts as a ``4401``.
A ``4401`` close drops the connection once and negotiates again for a fresh ticket, since two negotiations on a first visit can issue different anonymous identities and the browser keeps the cookie of the last.
A second ``4401`` with no ``hello`` between the two sends the connection to SSE until a resume reopens it, and each ``hello`` earns one such retry.
The retry does not count as a failed attempt.

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

The runtime reads one attribute namespace, ``data-next-*``, and twenty-one names make up the whole contract.
They fall into authorship groups, and a reader needs the split to know which are theirs to write.
The server writes six of them from ``{% zone %}`` and ``{% form %}`` alone, the runtime writes four as styling and state hooks, and seven are hand-authored on plain markup.
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
     - Hand-written on a pagination link or sentinel
     - The triggers, which GET the link with an ``append`` or ``prepend`` intent so the server authors the merge patch.
   * - ``data-next-confirm``
     - Hand-written on a link or a submit control
     - The triggers, which run the confirm gate on the nearest ancestor carrying it and cancel the interaction on a refusal.
   * - ``data-next-keep``
     - Hand-written on any element
     - The morph engine, which leaves the node and its subtree untouched so a mounted island root survives.
   * - ``data-next-stream``
     - Hand-written on a container
     - The stream bridge, which opens one stream per distinct URL, over WebSocket when negotiation succeeds and over ``EventSource`` otherwise, and rescans each inserted subtree.
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
:doc:`template-tags` records the ``{% zone %}`` and ``{% form %}`` parameters that compile to the ten names a tag can write.

Exported types
~~~~~~~~~~~~~~

The entry module exports nineteen type names.
``NextContext``, ``NextEventMap``, and ``NextChunks`` are its own, and ``PartialError`` and ``PartialErrorKind`` come from the protocol module, which is where the client and the server agree on the wire vocabulary.
``Envelope``, ``Patch``, ``Asset``, ``AssetLoad``, and ``FormMeta`` come from the apply module and type the envelope a parse hook or a ``partial:before-apply`` listener handles.
``NavigatedDetail``, ``NavigationState``, ``NavigationAction``, ``ConsentChange``, ``ScriptsChunk``, ``ScriptStatus``, and ``StreamMeta`` come from the navigation, consent, chunk, scripts, and stream modules, see :doc:`client-extras` and :doc:`/content/topics/realtime`.
``Outcome`` comes from the wire module and types the result of ``fetch`` and ``submit``, and ``SubmitAction`` comes from the partial module and types the argument of ``submit``.
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
     "partial:before-apply": { envelope: Envelope };
     "partial:applied": { envelope: Envelope; ok: boolean; nodes: readonly Element[] };
     "partial:error": PartialError;
     "partial:layer-opened": { opener: HTMLElement | null };
     "partial:layer-accepted": { result: unknown };
     "partial:layer-dismissed": { reason: string };
     "next:stream-gap": { topic: string; from: number; to: number };
     "next:stream-state": {
       url: string;
       transport: "sse" | "ws";
       state: "connecting" | "open" | "closed";
       code?: string;
     };
     "next:toast": { text: string; variant: string };
     "next:navigated": NavigatedDetail;
     "next:consent": ConsentChange;
     "next:script-loaded": { name: string };
     "next:script-error": { name: string; url: string };
   }

   export type { PartialError, PartialErrorKind } from "./protocol";
   export type { NavigatedDetail, NavigationAction, NavigationState } from "./navigation";
   export type { Asset, AssetLoad, Envelope, FormMeta, Patch } from "./apply";
   export type { ConsentChange } from "./consent";
   export type { ScriptsChunk } from "./chunks";
   export type { ScriptStatus } from "./scripts";
   export type { StreamMeta } from "./sse";
   export type { Outcome } from "./wire";
   export type { SubmitAction } from "./partial";

``PartialError`` is the discriminated union every ``partial:error`` payload inhabits, over the ``network``, ``http``, ``parse``, ``op``, ``asset``, ``csrf``, and ``undelivered`` kinds.
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
     - The delegated ``data-next-*`` handlers, lazy zone activation, the fetch of the poll chunk, and inline validation.
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
   * - ``protocol.ts``
     - The wire vocabulary shared with the server, the content type, the headers, the ``PartialError`` union, and ``pageKey``, the same-origin path and query a layer and a zone are keyed by.
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
   * - ``entries/scripts.ts``, ``entries/stream.ts``, ``entries/ws.ts``, ``entries/csrf.ts``, ``entries/poll.ts``, ``entries/dev.ts``
     - The entry of each lazy chunk, one module per chunk named after it.

Configuration
-------------

Nothing configures the runtime from the browser side.
``NEXT_JS_OPTIONS`` inside ``NEXT_FRAMEWORK`` decides how the bundle and the inline ``_init`` payload reach the page, over the ``auto``, ``manual``, and ``disabled`` injection policies and the three tag templates.
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
