.. _topics-partial-rendering-reference:

Partial rendering reference
===========================

The envelope fields, the patch verbs, the request and response headers, the ``data-next-*`` attributes, and the ``PARTIAL_BACKENDS`` settings, in tables.
For the narrative behind any of these, read the scenario that uses it in :doc:`scenarios`.

.. contents::
   :local:
   :depth: 1

Envelope fields
---------------

Every partial response is one JSON envelope, and the six top-level keys below are its whole surface.
A protocol backend that authors its own wire format carries the same fields under whatever names it chooses.

.. list-table::
   :header-rows: 1
   :widths: 18 22 60

   * - Field
     - When
     - Semantics
   * - ``version``
     - Always
     - The asset version the envelope was built under, the value the client compares against the one it holds.
   * - ``ops``
     - Always
     - The ordered patch list, ``[]`` when the response carries no operation.
   * - ``assets``
     - Always
     - The asset manifest of the rendered targets, ``[]`` when empty.
   * - ``form``
     - Always
     - The form meta of a submitted action, its ``uid``, ``valid`` flag and ``errors`` mapping, and ``null`` when the response answers no form.
   * - ``csrf``
     - A token rotation
     - The ``header`` and ``token`` pair the runtime switches to.
       The client rewrites every ``csrfmiddlewaretoken`` input in the document and sends the new token on the next mutation.
   * - ``request_id``
     - A stream envelope
     - The id of the mutation the envelope echoes, which a subscriber matches against its echo ring to drop its own write, see :doc:`sse`.

The JSON examples on this page omit ``assets`` and ``form`` when they are empty.

Patch verbs
-----------

A patch is one addressed DOM operation with a verb, an optional target, optional HTML, and verb-specific extras.
The operations apply in list order.
The server is the only author of a target, the client never names one.
``Patches(request)`` opens the builder in a handler.
``Patches.versioned(version, *, echo_of=None, request=None)`` opens the same builder for a path that already holds the asset version and renders its own HTML, pinning that version literally instead of resolving it.
Pass ``request`` whenever one exists, so the asset URLs of the envelope stay scoped per request, and ``echo_of`` to stamp the request id a stream envelope echoes.

.. list-table::
   :header-rows: 1
   :widths: 16 30 40 14

   * - Verb
     - Builder method
     - Semantics
     - Default
   * - ``morph``
     - ``morph()``
     - The default verb.
       Morph the target into the HTML.
       The target names a zone or a form by uid.
       ``extract: true`` carries a whole document the client trims to the target, and ``morph_form(uid, html)`` is the builder route that sets it.
       ``morph(zone=, overrides=)`` merges a mapping into the zone's render context, so a handler can bind a value the zone body reads without registering a provider for it.
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
     - Ask the client to re-fetch the zone with its own cookies.
       The safe default of an SSE fan-out.
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
       The text is set as ``textContent`` and never parsed as HTML.
     - ``variant: "info"``
   * - ``layer.open``
     - ``layer_open()``
     - Open a layer from the server, optionally seeding a zone to fill later or an href whose zone loads into the modal.
       See :ref:`partial-server-layers`.
     - none
   * - ``layer.close``
     - ``layer_close()``
     - Close the top layer with an accept result or a dismissal.
     - accept, no result
   * - ``url``
     - ``push_url()``
     - Push browser history.
       The href is validated by the server.
       The client also honours ``action: "replace"``, reachable only from a raw or backend-authored envelope, the ``push_url()`` builder always pushes.
     - ``action: "push"``
   * - ``visit``
     - ``redirect()``
     - A full client navigation to a server-authored href.
       ``external=True`` skips same-host validation, see :ref:`security-overview`.
     - none

A target carries exactly one address key, and the client resolves ``zone``, then ``form``, then ``field``, then ``css``.
``zone`` names a ``data-next-zone`` wrapper and ``form`` names an action uid.
``field`` is a ``[uid, name]`` pair addressing one named input of a form by its uid.
``css`` is a raw selector, the escape hatch a bare layer shell relies on.
No ``Patches`` builder method produces a field target, so a handler that needs one constructs the ``Patch`` by hand and appends it to the envelope.

.. code-block:: python

   Patch(op="morph", target={"field": [uid, "email"]}, html=rendered_input)

A handler that returns ``None`` under the runtime also drains the pending :doc:`django.contrib.messages <django:ref/contrib/messages>` into ``toast`` patches, one per message, with the message level mapped to the toast variant.
The drained variants are ``info``, ``success``, ``warning``, and ``error``, with ``debug`` mapped to ``info``.
An action that already sets ``Meta.success_message`` therefore shows a toast without a builder call.
Draining marks the messages read, so a later full navigation does not replay them.

A verb beyond this set is registered on both sides.
``register_patch_op("confetti")`` on the server registers the name and earns the generic ``op()`` channel on the builder.
``manage.py check`` reads the registered names, reporting ``next.E066`` for a name that shadows a built-in verb and ``next.E090`` for a name that is not a valid verb token.
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
A reference a zone body registers, through ``{% use_style %}``, ``{% use_script %}``, or a module-level list, is resolved by the same backend on both paths, so the ``url`` of an entry is the URL a full render prints for that asset.
``Patches.add_asset(kind, url, inline=None)`` is the route by which a handler authors an entry of its own, and the ``url`` it takes passes that same resolution, so a logical name reaches the manifest as the URL a full render would have written, see :doc:`/content/topics/static-assets/name-resolution`.

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
     - The URL form of the asset.
       An inline asset carries an empty string here.
   * - ``inline``
     - An asset body
     - The body of an inline asset, absent on a URL-form asset.
   * - ``load``
     - ``link``, ``script``, or ``module``
     - The insertion verb, derived from the renderer registered for the kind.
       Absent when that renderer is a custom backend method, and absent on an inline body whose kind does not wrap it in the element the verb builds.

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
Every request goes to an absolute URL on the page's own origin with ``mode: "same-origin"``, so these headers never leave the site.
A target off that origin is refused before any request leaves, with a ``partial:error`` of kind ``network``.

.. list-table::
   :header-rows: 1
   :widths: 26 24 50

   * - Header
     - When
     - Semantics
   * - ``X-Next-Request: 1``
     - Every intercepted request
     - The partial switch.
       Without it the response is the full page, byte for byte.
   * - ``Accept``
     - Every intercepted request
     - ``application/vnd.next.patches+json, text/html;q=0.9``, the switch at the content-negotiation level.
   * - ``X-Next-Zone``
     - Zone GET, poll, refresh, filter, and a zoned form submission
     - The zones to render, comma-joined for a batch.
   * - ``X-Next-Validate``
     - Inline validation
     - The field names to validate without saving.
   * - ``X-Next-Merge``
     - Pagination
     - ``append`` or ``prepend``, the merge intent.
   * - ``X-Next-Version``
     - Every request once a version is learned
     - The asset version the client holds.
       The first request of a page asserts none.
   * - ``X-Next-Request-Id``
     - Every mutation
     - The ring id used to suppress an SSE echo.
   * - ``X-Next-Origin``
     - Every layer request, the open GET, the accept re-GET, and a mutation submitted from a form inside the layer.
     - The path and query string of the page that hosts a layer, for a server-side morph of its zones.
       The server validates the value as sent as a same-site path, then splits off the query and decodes the path as Django decodes ``request.path``.
       A header that does not resolve to a page falls back to the posted form origin.
   * - CSRF header
     - Every unsafe method once the runtime holds a token
     - The name comes from ``CSRF_HEADER_NAME``, the token from the ``$csrf`` init payload and from any later rotation meta, the cookie is never read.

An inline-validation POST carries an internal ``validate:<uid>`` value in ``X-Next-Zone`` instead of a declared zone, and the server ignores that name because no page declares it.
A batch renders every declared name it carries and drops the rest, so one stale name never poisons a sweep.
Only a batch in which no name is declared is a 400.

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
     - Every response from a zone-capable URL
     - ``X-Next-Request, X-Next-Zone, X-Next-Merge, X-Next-Version``, set on every envelope, on the 400 and 409 short-circuits, on a wizard advance, and on an SSE stream, so a shared cache never hands one intent's envelope to another.
       A full page render from the same URL declares the same set, because the page and the envelope share an address and a cache that stored the unvaried HTML would answer a later partial request with it.
       A page whose ``render()`` returns its own :class:`~django.http.HttpResponseBase` short-circuits before the shaper and stamps nothing, so such a view calls ``set_partial_vary(response)`` from ``next.partial.headers`` when the same URL also answers partial requests.
       The three headers that never change the body, ``X-Next-Validate``, ``X-Next-Origin``, and ``X-Next-Request-Id``, stay out of the set.
   * - ``X-Next-Version``
     - Every envelope
     - The current asset version.
   * - ``X-Next-Form: invalid``
     - An invalid form
     - The marker of an invalid submission, always the literal ``invalid``, stamped alongside ``X-Next-Action``.
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
     - Patches, including an invalid form.
       A validation error is state, not an HTTP failure.
   * - 200 without an envelope
     - The fetch followed a redirect, a guard bounce or a login wall.
       The runtime performs a full navigation to ``response.url``, once, under the navigate-once flag.
   * - 204
     - A success with no patch to apply, for example a wizard advance with no redirect target.
       The runtime applies nothing.
   * - 303
     - A mutation succeeded with no runtime on the page, the plain ``POST`` then ``303`` then ``GET`` cycle.
   * - 403 without an envelope
     - A guard denial or a CSRF failure served outside the shaping path.
       On a mutation the runtime stays in place and fires ``partial:error`` with the status and body.
       On a safe method it navigates fully.
   * - 400
     - An intent that did not validate, such as a zone no name in the batch declares, a bad origin, or a zone named on a dynamic page body.
   * - 404
     - The request named a form uid the action registry does not hold.
   * - 409
     - A version mismatch on a safe method, with an empty body.
       The runtime fully visits the URL the request was made against, which is the page that owns the zone rather than whatever the address bar holds.
       A mutation always runs.
       The version mismatch then travels in the envelope version, and the client answers it with one full navigation under the reload-once flag.
   * - 5xx
     - No envelope.
       The runtime swaps nothing and fires ``partial:error``.

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
     - ``load`` or ``revealed``, the materialisation trigger.
       On a zone wrapper ``load`` fetches on ``ready`` and ``revealed`` waits for the viewport.
       On a pagination sentinel the attribute only marks the link observable, either value arms the observer that fires the merge GET.
       On a zone wrapper a value outside the two is ignored.
       On a sentinel the value is not read at all, and either way a dev build warns about an unrecognised value.
   * - ``data-next-poll``
     - Zone wrapper
     - The poll interval in milliseconds, from the ``poll=`` literal, written on the full render and on the partial response wrapper.
       The runtime re-GETs the zone on the interval while the tab is visible.
       A hand-written value outside the whole-millisecond grammar, below the one-second floor, or above the browser timer ceiling is dropped, as is the attribute on an element without ``data-next-zone``, each with a console warning in dev.
   * - ``data-next-action``
     - ``<form>``
     - The action uid, written by ``{% form %}``, enables submit interception.
   * - ``data-next-validate``
     - ``<form>``
     - Inline validation on blur, written from the ``validate=`` tag parameter.
       The runtime keys off the presence of the attribute alone and never reads its value, so every value behaves the same and there is no closed set to choose from.
       The examples write ``blur`` because blur is the trigger.
   * - ``data-next-target``
     - ``<a>``, ``<form>``
     - Route the response into a zone.
       On a GET filter it names the zone to morph, and on a POST form it is written by the ``zone=`` tag parameter and travels as the morph target of the submission.
   * - ``data-next-trigger``
     - Filter ``<form>``, sort ``<select>``
     - The event that auto-submits a GET filter, ``input`` or ``change``.
       Submit and click interception are wired by ``data-next-action`` and ``data-next-merge``, not this attribute.
   * - ``data-next-debounce``
     - With ``data-next-trigger``, or on a validating ``<form>``
     - Debounce in milliseconds.
       On a filter it collapses a burst of keystrokes into one GET, on a validating form it collapses a burst of blur probes into one validate POST.
   * - ``data-next-merge``
     - Pagination link
     - ``append`` or ``prepend``, travels as ``X-Next-Merge``.
       Any other value is ignored, with a console warning in dev.
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
       On a repeated form it names the instance, written by the ``key=`` tag parameter, so a partial morph lands on the submitted form rather than the first.
   * - ``data-next-sse``
     - A container
     - Subscribe to a patch stream at the URL.
   * - ``data-next-busy``
     - Layer opener, layer zone container
     - Written during a layer open on the opener link and the layer's zone container, alongside ``aria-busy="true"``.
       The submit double-click guard is the per-uid mutation lock, not this attribute.
   * - ``data-next-dialog``
     - Runtime ``<dialog>``
     - Set by the runtime on every layer dialog, the styling hook for the modal shell.
   * - ``data-next-toasts``
     - Runtime toast container
     - The toast tray, created by the runtime on the first ``toast`` and the styling hook for the stack.
       The runtime also stamps ``aria-live="polite"`` on it, so a replacement tray carries its own live region.
   * - ``data-next-toast``
     - Runtime toast item
     - One toast, the value is the variant, the styling hook for a single notification.
       The text is set as ``textContent`` and never parsed as HTML, so a toast string cannot carry markup.

.. warning::

   ``data-next-validate`` carries no off switch, unlike the closed value sets of ``data-next-lazy`` and ``data-next-merge``.
   Writing ``validate="off"`` on the tag turns inline validation on, because the rendered attribute is present either way.
   Omit the ``validate=`` parameter to leave inline validation off.

Lifecycle events
----------------

The runtime fires events on three channels, the element, the document, and the ``Next.on`` bus.
The ``next:*`` node events fire on the element as a bubbling ``CustomEvent`` caught with ``addEventListener``.
The apply-stage ``partial:*`` events and ``next:toast`` fire on the document and the ``Next.on`` bus.
A ``partial:error`` of kind ``asset``, raised when a co-located stylesheet fails to load, when the asset version still mismatches after the reload, or when the reload target sits off the page's origin, reaches only the bus.
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
     - The seeded client context, the same object ``Next.context`` exposes.
       A listener added after the runtime is ready is replayed with it at once.
   * - ``context-updated``
     - No
     - ``{context, changed}``, where ``context`` is the whole merged store and ``changed`` lists only the keys of the delta that arrived.
       The initial seed lists every seeded key in ``changed``.
   * - ``partial:before-request``
     - No
     - ``{url, method, intent}``, where ``intent`` is ``{zone?, uid?}``.
       The runtime fires this through the bus before the fetch leaves, so a listener observes the request rather than vetoing it.
   * - ``partial:before-apply``
     - Yes
     - ``{envelope}``, the op list is mutable.
       The veto lives on the document event, so only a ``document.addEventListener`` listener can call ``preventDefault()``, while a ``Next.on`` listener merely observes.
   * - ``partial:applied``
     - No
     - ``{envelope, ok}``.
       ``ok`` is ``false`` when any op threw or named an unknown verb, so a listener tells a clean apply from a degraded one that still mounted what did change.
       Observe ``ok``, not the bare fact of apply.
   * - ``partial:error``
     - No
     - A discriminated union on ``kind``, where each cause carries only its own fields.
       ``{kind: "network", error, url?}`` is a fetch reject, a dropped stream connection, a zone that still answers a non-envelope after the navigate-once fallback already navigated, or a target off the page's origin refused unsent, where ``url`` is present only on that refusal.
       ``{kind: "http", status, body}`` is a 5xx or a mutating reply that is not an envelope.
       ``{kind: "parse", body, error}`` is a malformed JSON body.
       ``{kind: "op", op, error, target?}`` is a thrown or unknown verb mid-apply, where ``op`` names the verb and ``target`` is the human-readable address of the patch, present only when the op carried a recognised target.
       ``{kind: "asset", error, url?}`` is a stylesheet that failed to load, a version mismatch surviving a reload, or a reload target off the page's origin, where ``url`` is present only on the last two.
       The ``status`` and ``body`` fields belong to ``http`` alone, and ``body`` also to ``parse``, so a listener branches on ``kind`` before reading them.
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
     - Fired on each touched node, bubbles.
       The node is the event target.
       Pairs with ``next:removed`` as the mount half of a framework island lifecycle.
   * - ``next:removed``
     - No
     - Fired on a node immediately before it detaches, bubbles, no detail.
       The unmount half of the island lifecycle, the place to tear down a mounted root or a timer.
   * - ``next:morph-element``
     - Yes
     - Fired on the old node before a pair morphs.
       Detail ``{newNode}``.
       ``preventDefault()`` skips the morph of this node and its subtree.
   * - ``next:morph-attribute``
     - Yes
     - Fired on the old element before one attribute changes.
       Detail ``{name, mutationType}``, where ``mutationType`` is ``"update"`` or ``"remove"``.
       ``preventDefault()`` skips that one attribute mutation.
   * - ``next:toast``
     - No
     - Detail ``{text, variant}``.
       The ``toast`` verb fires it on the document and the ``Next.on`` bus alongside building the toast.

The mount and morph events run during the patch apply, so a framework island can take over a node by vetoing its morph and managing its own subtree.
The mounted and removed pair brackets the node's life inside the document, the symmetry an adapter relies on to mount and unmount a root.

Client runtime
--------------

The runtime exposes ``window.Next`` once the bundle loads, and :doc:`/content/ref/client` records that surface in full, its members, its layer and stream objects, and how the bundle is shipped.
What belongs here is the part of the runtime that observes the protocol this page describes.

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

Settings
--------

The partial subsystem reads ``PARTIAL_BACKENDS`` inside ``NEXT_FRAMEWORK``.
The list holds the protocol backends, and only the first entry is active.
The rest are ignored, multi-backend selection is not supported, and a list with more than one entry earns the ``next.W071`` warning at ``manage.py check``.

.. code-block:: python
   :caption: the default

   [
       {
           "BACKEND": "next.partial.JsonPartialProtocolBackend",
           "OPTIONS": {
               "VERSION": None,
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
     - ``None``
     - The source of ``X-Next-Version``.
       The default derives it from ``NEXT_FRAMEWORK["STATIC_VERSION"]``, falls back to the hash of the staticfiles manifest when the active storage hashes its files, and resolves the stable string ``"0"`` when neither is there, which ``next.W083`` reports on a deployment audit.
       The sentinel ``"manifest"`` skips the deploy stamp and demands the manifest, which is the requirement ``next.W069`` enforces, and any other string pins a release tag of its own.
   * - ``PUSH_WIZARD_STEPS``
     - ``False``
     - The global default for pushing wizard steps to history.
       A wizard's ``Meta.push_steps`` overrides it per wizard.
   * - ``SSE.HEARTBEAT_SECONDS``
     - ``25``
     - The heartbeat period for an async source.
   * - ``SSE.RETRY_MS``
     - ``3000``
     - The ``EventSource`` reconnect hint.

See :doc:`/content/ref/settings` for every key inside ``NEXT_FRAMEWORK``.

See also
--------

.. seealso::

   :doc:`scenarios` for each verb, header, and attribute in the context of a task.
   :doc:`/content/ref/client` for the ``window.Next`` surface that applies every envelope.
   :doc:`layers` for the modal narrative behind the layer verbs and attributes.
   :doc:`/content/ref/system-checks` for the zone and verb checks.
   :doc:`/content/topics/signals` for the partial subsystem signals.
