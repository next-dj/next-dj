.. _topics-partial-rendering-reference:

Partial rendering reference
===========================

The envelope fields, the patch verbs, the request and response headers, the ``{% more %}`` options, the ``data-next-*`` attributes, and the ``PARTIAL_BACKENDS`` settings, in tables.
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
       The ``errors`` keys are field names with non-field errors under ``__all__``.
       A formset prefixes each row, as in ``form-1-title`` and ``form-1-__all__``, keeps its own errors under ``__all__``, and skips a row marked for deletion.
   * - ``csrf``
     - A token rotation
     - The ``header`` and ``token`` pair the runtime switches to.
       The client rewrites every ``csrfmiddlewaretoken`` input in the document and sends the new token on the next mutation.
   * - ``request_id``
     - A stream envelope
     - The id of the mutation the envelope echoes, which a subscriber matches against its echo ring to drop its own write, see :doc:`sse`.

Stream frames add transport metadata beside the envelope.
``server_time`` is an epoch timestamp in milliseconds on every ``next-patches`` frame.
A realtime topic frame also has ``topic``, ``epoch``, and ``seq``.
These keys are absent from ordinary HTTP response bodies and are not fields of ``Envelope``.

.. list-table:: Realtime stream control frames
   :header-rows: 1
   :widths: 24 76

   * - Event
     - Data
   * - ``next-hello``
     - ``protocol: 2``, ``server_time``, ``topics`` mapping each topic name to ``{"epoch": ..., "seq": ...}``, and the supported ``transports``.
       It is sent once the broker subscription is live.
   * - ``next-skip``
     - ``topic``, ``epoch``, and ``seq`` acknowledge a message suppressed by a subscriber transform.
   * - ``next-end``
     - ``code``, ``"auth"`` when the topic authorizer denied a grant.
       The client closes the stream and does not reconnect it.
       A revoked session sends no ``next-end``, so the reconnect goes through the page guard again.
       Only an SSE stream carries it.
       A WebSocket sends no end frame, and a denied grant there closes the socket with ``4403`` after an ``error`` frame, which sends that stream to SSE for good.
   * - ``next-patches``
     - The ordinary envelope plus ``server_time``, and on topic streams ``topic``, ``epoch``, and ``seq``.

The control frames are compact JSON in this fixed schema whatever protocol backend is installed.
A WebSocket carries the hello, skip, and patches frames as JSON objects with a ``type`` of ``hello``, ``skip``, or ``patches``, see :doc:`/content/ref/realtime`.

The client keeps an ``(epoch, seq)`` cursor per topic and ignores duplicates.
It requests the page's bound zones after a gap in the same epoch, a change between two non-empty epochs, a nonzero sequence on the first hello, or a changed position on reconnect.
A live connection with no resync in flight adopts a new epoch that starts at ``seq`` ``1`` without a request, see :ref:`ref-client-stream`.
An epoch change means the broker counter started again, so the client resets that topic's cursor to the new position.
A reconnect replays only what the broker's bounded log still holds, see :doc:`/content/topics/realtime`.
A zone GET restores current state through the page's guards and the subscriber's cookies.

Patch verbs
-----------

A patch is one addressed DOM operation with a verb, an optional target, optional HTML, and verb-specific extras.
The operations apply in list order.
The server is the only author of a target, and the client never names one.
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
       ``morph(zone=)`` follows the morph with the ``context`` op a zone GET sends, ``context=`` merges a mapping of serialize-provider values over it, and ``context=False`` leaves the client context alone.
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
     - Show a toast in the page's ``[data-next-toasts]`` tray, or in one the runtime builds when the page renders none.
       The text is set as ``textContent`` and never parsed as HTML, and the item goes through the mount pass, so ``onMount`` and ``next:mounted`` reach it.
     - ``variant: "info"``
   * - ``layer.open``
     - ``layer_open()``
     - Open a layer from the server, optionally seeding a zone to fill later or an href whose zone loads into the modal.
       The GET of the href selects an intercepting route the same way a ``data-next-layer`` link does, see :doc:`layers`.
       See :ref:`partial-server-layers`.
     - none
   * - ``layer.close``
     - ``layer_close()``
     - Close the top layer with an accept result or a dismissal.
     - accept, no result
   * - ``url``
     - ``push_url()``, ``replace_url()``
     - Push or replace the browser history entry, with ``action: "push"`` or ``action: "replace"``.
       The href is validated by the server, and the write waits for the envelope to commit, so one ``next:navigated`` announces it.
       An operation with ``quiet: true`` writes the address without announcing a page view, which is how a ``{% more %}`` batch records its position.
     - ``action: "push"``
   * - ``visit``
     - ``redirect()``
     - A full client navigation to a server-authored href.
       ``external=True`` skips same-host validation, see :ref:`security-overview`.
     - none
   * - ``meta``
     - ``meta()``
     - Sync the title, the description, the canonical link, and the robots meta the origin page would render with the given text or ``MetadataDict`` folded over its chain as the page's own segment.
       The operation carries ``title``, ``description``, ``canonical``, and ``robots``, a ``null`` removing the tag and an absent key leaving it alone.
       No markup travels, see :doc:`/content/topics/seo/metadata`.
       An inherited metadata callable of an ancestor runs as in the render, after the guard of the origin page.
       A zone GET served by an intercept page carries one ``meta`` patch with the head of that page.
       The tags belong to the page whose envelope carried them, and a ``meta`` for a URL that is neither the page nor an open layer is dropped, see :doc:`layers`.
     - none

.. warning::

   The ``html`` of ``morph``, ``replace``, ``inner``, ``append``, and ``prepend`` is inserted as given and never escaped.
   A handler escapes untrusted text before it passes it as ``html``, or renders the markup through a template that escapes.
   Only ``toast`` sets its text as ``textContent``.

A target carries exactly one address key, and the client resolves ``zone``, then ``form``, then ``field``, then ``css``.
``zone`` names a ``data-next-zone`` wrapper and ``form`` names an action uid.
``field`` is a ``[uid, name]`` pair addressing one named input of a form by its uid.
``css`` is a raw selector, the escape hatch a bare layer shell relies on.
Every builder verb that takes a target mapping accepts a field target as written.

.. code-block:: python
   :caption: a handler building a field target

   from django.http import HttpRequest, HttpResponse

   from next.partial import Patches

   def morph_email(request: HttpRequest, uid: str, rendered_input: str) -> HttpResponse:
       """Morph the email input of the form addressed by its uid."""
       return Patches(request).morph({"field": [uid, "email"]}, rendered_input).response()

A handler that returns ``None`` under the runtime also drains the pending :doc:`django.contrib.messages <django:ref/contrib/messages>` into ``toast`` patches, one per message, with the message level mapped to the toast variant.
The drained variants are ``info``, ``success``, ``warning``, and ``error``, with ``debug`` mapped to ``info``.
An action that already sets ``Meta.success_message`` therefore shows a toast without a builder call.
Draining marks the messages read, so a later full navigation does not replay them.

A verb beyond this set is registered on both sides.
``register_patch_op("confetti")`` on the server registers the name and earns the generic ``op()`` channel on the builder.
``register_patch_op`` raises ``BuiltinPatchOpError`` for a built-in verb name, because the client applies the built-in verb and a custom handler under that name would never run.
``manage.py check`` reads the registered names and reports ``next.E090`` for a name that is not a valid verb token.
An unregistered name fails at runtime with ``UnknownPatchOpError``.
``Next.partial.defineOp("confetti", handler)`` on the client supplies the handler.
See :doc:`extending` for the end-to-end recipe, the ``context`` and ``event`` seams, and the custom-verb exceptions.

An event name that starts with ``partial:`` or ``next:``, or equals ``ready`` or ``context-updated``, is reserved for the runtime lifecycle.
``Patches.event()`` rejects such a name with ``ReservedEventNameError``, symmetric to ``op()`` rejecting a built-in verb on the generic channel, so an application cannot forge a lifecycle event.

The ``$csrf``, ``$dev``, ``$chunks``, ``$realtime``, ``$scripts``, and ``$consent`` keys of the init payload are reserved the same way.
``Patches.context()`` and ``morph(zone=, context=)`` reject any of them with ``ReservedContextKeyError``, and the js-context delta of a zone render drops them before it becomes a ``context`` op.
The ``$`` namespace therefore belongs to the framework on a patch exactly as it does on a full render.
A full render drops a page or component key of such a name from the payload whether or not it has a framework value to write there, so no patch has a registered value to update.

Asset manifest
--------------

The ``assets`` key of an envelope lists the co-located assets the rendered targets registered.
Each entry carries ``kind`` and ``url`` always, plus ``inline`` and ``load`` when they apply.
A reference a zone body registers, through ``{% use_style %}``, ``{% use_script %}``, or a module-level list, is resolved by the same backend on both paths, so the ``url`` of an entry is the URL a full render prints for that asset.
``Patches.add_asset(kind, url, *, inline=None)`` is the route by which a handler authors an entry of its own, and the ``url`` it takes passes that same resolution, so a logical name reaches the manifest as the URL a full render would have written, see :doc:`/content/topics/static-assets/name-resolution`.

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
       On a GET or a HEAD the zone names also select an intercepting route of the page, see :ref:`topics-file-router-intercepts`.
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
     - Every non-abortable mutation
     - The ring id used to suppress an SSE echo.
   * - ``X-Next-Transport: ws``
     - The GET of a stream URL while WebSockets are enabled
     - Asks for a socket ticket in place of the stream, sent with ``Accept: application/json``, see :ref:`topics-realtime-tunnel` and :doc:`/content/ref/realtime`.
   * - ``X-Next-Origin``
     - Every layer request, the open GET, the accept re-GET, and a mutation submitted from a form inside the layer.
     - The path and query string of the page that hosts a layer, for a server-side morph of its zones.
       The server validates the value as sent as a same-site path, then splits off the query and decodes the path as Django decodes ``request.path``.
       A header that does not resolve to a page falls back to the posted form origin.
       A client router navigation sends it too, naming the page it leaves, see :doc:`/content/ref/router`.
   * - CSRF header
     - Every unsafe method once the runtime holds a token
     - The name comes from ``CSRF_HEADER_NAME``, the token from the ``$csrf`` init payload, from ``/_next/csrf/`` when the payload defers it, and from any later rotation meta, and the cookie is never read.

An inline-validation POST sends ``X-Next-Zone`` only when its form carries ``data-next-target``, and otherwise the server answers with the extract default.
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
     - The request headers a shared cache keys the response on, listed per response kind in :ref:`topics-partial-rendering-reference-vary`.
   * - ``X-Next-Version``
     - Every envelope
     - The current asset version.
   * - ``X-Next-Form: invalid``
     - An invalid form
     - The marker of an invalid submission, always the literal ``invalid``, stamped alongside ``X-Next-Action``.
   * - ``X-Next-Action``
     - An invalid form
     - The uid of the failed action.
   * - ``X-Next-Transport``
     - A tunneled request, and a reply of an action declared ``transport="any"``
     - ``http`` on a ``421`` refusal of an action that answers over HTTP only, ``any`` on the reply of an opted-in action, and ``session`` on a ``421`` for an action that rotated or flushed the session, see :ref:`topics-forms-actions-transport`.

.. _topics-partial-rendering-reference-vary:

Vary by response kind
---------------------

A page, its zones, its navigation envelope, and its boundary reloads share one URL, so each response names in ``Vary`` the request headers that chose its body.
The partial set is ``X-Next-Request, X-Next-Zone, X-Next-Merge, X-Next-Version, X-Next-Boundary, X-Next-Navigate, X-Next-Layouts, X-Next-Prefetch``, exported as ``VARY_HEADERS`` from ``next.partial.headers``.
``X-Next-Validate`` and ``X-Next-Request-Id`` never enter ``Vary``, and ``X-Next-Origin`` enters it only where the table says.

.. list-table::
   :header-rows: 1
   :widths: 24 76

   * - Response
     - Request headers in ``Vary``
   * - Full document
     - The partial set, because a cache that stored the unvaried HTML would answer a later partial request with it.
       A response ``render()`` returns gets the set only when it is a successful :class:`~django.http.HttpResponse` that carries ``Cache-Control``, so a view that answers otherwise calls ``set_partial_vary(response)`` from ``next.partial.headers`` when the same URL also answers partial requests.
   * - Streamed document
     - The partial set, ``Accept``, and ``Accept-Encoding``.
   * - Zone response
     - The partial set, ``Accept`` on a ``GET`` or ``HEAD`` because a zone read may answer a stream by it, and ``X-Next-Origin`` while the active-link pass of ``ROUTER["CURRENT"]`` marks the links against the origin.
       The 400 and 409 short-circuits and the refusal of an intercept carry the partial set, plus ``X-Next-Origin`` under the active-link pass.
   * - Streamed zone response
     - The partial set, ``Accept``, ``Accept-Encoding``, and ``X-Next-Origin`` under the active-link pass.
   * - Form result
     - The partial set on every answer to a submission that sends ``X-Next-Request``, the invalid re-render and a wizard advance included.
       A submission without it gets the handler's response unvaried.
   * - Navigation envelope
     - The partial set, on the envelope, on a redirect passed to the router, and on the 409 of a stale version.
       ``X-Next-Origin`` stays out, because the declared layouts in ``X-Next-Layouts`` decide the cut.
   * - Streamed navigation
     - The partial set, ``Accept``, and ``Accept-Encoding``.
   * - Prefetch
     - The partial set, on the envelope, on the refusal of a page that opts out, and on the 429 of a lagging pool.
   * - Boundary reload
     - The partial set on every answer, the refusals included, and ``Accept`` on a ``GET`` or ``HEAD`` answered whole.
       A reload that streams adds ``Accept`` and ``Accept-Encoding``.
   * - Realtime event stream
     - The partial set.
       The ticket answer of a stream URL varies on ``Cookie`` and ``X-Next-Transport``.

Django adds ``Cookie`` to any of these when the session or the CSRF cookie is read, and the compression layer adds ``Accept-Encoding`` to a body it compresses.

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
   * - 400
     - An intent that did not validate, such as a zone no name in the batch declares, a bad origin, or a zone named on a dynamic page body.
   * - 403 without an envelope
     - A guard denial, an access rule of the page, or a CSRF failure served outside the shaping path.
       On a mutation the runtime stays in place and fires ``partial:error`` with the status and body.
       On a safe method it fires the same ``partial:error`` and then navigates fully.
   * - 404
     - The request named a form uid the action registry does not hold, or the page answered a zone GET with 404, such as a ``DObject`` row outside the scope.
       On a safe method the runtime fires ``partial:error`` and then navigates fully, and on a mutation it stays in place after the event.
   * - 409
     - A version mismatch on a safe method, with an empty body.
       The runtime fully visits the URL the request was made against, which is the page that owns the zone rather than whatever the address bar holds.
       A mutation always runs.
       The version mismatch then travels in the envelope version, and the client answers it with one full navigation under the reload-once flag.
   * - 421
     - A tunneled request to an action that answers over HTTP only, or one that rotated or flushed the session.
       For the first nothing ran, and the client repeats the request over ``fetch``.
       For the second the runtime reloads the page, see :ref:`topics-forms-actions-transport`.
   * - 5xx
     - No envelope.
       The runtime swaps nothing and fires ``partial:error``.

A safe-method zone GET that answers with a non-envelope navigates once under a navigate-once flag, the same shape the version-mismatch reload uses.
A component deferred with ``load`` asks again on the freshly loaded page, so an expired session, a WAF stub, or a maintenance page that keeps answering non-envelope would otherwise loop the navigation.
The second non-envelope while the flag stands degrades to a ``partial:error`` of kind ``network`` and leaves the page in place, and the flag clears the moment a correct envelope classifies.

.. _topics-partial-rendering-reference-more:

Infinite lists
--------------

``{% more zone="<zone>" next=<expr> %}label{% empty %}end{% endmore %}`` renders the sentinel of an infinite list as a zone named ``<zone>-more``.
The zone holds a real ``<a>`` whose ``href`` is the query string of the request with the paging parameter set to the token of the next batch and ``restore`` removed, so the link works without the runtime.
When the list has no next batch, the zone holds the ``{% empty %}`` branch in a ``<div>`` instead.
Both carry ``data-next-key="<zone>-more"``, so an ``append`` merge swaps one for the other.
The ``<zone>-more`` zone is declared like any ``{% zone %}`` of the template, so a zone request names it beside the list zone.
The runtime counts a zone request as a batch of the list only when its URL is the ``href`` of the link and its zones are exactly the ``data-next-target`` of the link.
Any other request that names the same zones, a live filter among them, is an ordinary zone request that stamps no row, prunes nothing and writes no ``restore=1``, whatever order it lists the zones in.

``{% more zone="<zone>" previous=<expr> %}label{% empty %}start{% endmore %}``, placed above the list, renders the zone ``<zone>-less`` the same way, with a visible link to the batch before the list.
It defaults to ``merge="prepend"`` and ``mode="manual"``, so the earlier batch lands above the rows on a click and a page opened at the top never fetches on its own.
On the first window its ``<div>`` holds the ``{% empty %}`` branch and, after it, a link to the first window with the ``hidden`` attribute, which the runtime reveals in place of the ``<div>`` once ``prune=`` first drops rows, so a list that started at the top can load its pruned rows back.
One tag takes either ``next=`` or ``previous=``, never both.

``next=`` and ``previous=`` take the ``Window`` a pager returns, and the tag reads the ``next`` or the ``previous`` token of the window and the query parameter the window names.
Either option also takes a raw token, which the tag writes under ``param=``.
A raw token is an integer from 1, a string of up to 18 ASCII digits read as that integer, or any other slug of letters, digits, ``-`` and ``_`` up to 512 characters.
``None`` and a missing variable end the list.
Any other value is a template bug, so the tag raises ``TemplateSyntaxError`` at render under ``DEBUG`` or ``STRICT_LOADING`` and renders the end branch otherwise.

.. list-table::
   :header-rows: 1
   :widths: 18 22 60

   * - Option
     - Default
     - Semantics
   * - ``zone``
     - Required
     - The quoted name of the list zone the fetched rows merge into.
       ``next.E220`` reports a name the page does not declare.
   * - ``next``
     - Required unless ``previous`` is set
     - A template expression for the batch after the list, usually the ``Window`` the list loops over.
   * - ``previous``
     - Required unless ``next`` is set
     - A template expression for the batch before the list, usually the same ``Window``.
   * - ``mode``
     - ``"visible"``, ``"manual"`` with ``previous``
     - ``visible`` writes ``data-next-defer="visible"`` so the runtime fetches when the link scrolls into view.
       ``manual`` leaves it out, so the link fetches on a click.
   * - ``merge``
     - ``"append"``, ``"prepend"`` with ``previous``
     - ``append`` or ``prepend``, written as ``data-next-merge``.
   * - ``margin``
     - None
     - One to four ``px`` or ``%`` lengths of up to six digits, written as ``data-next-margin``.
       A bare integer of up to six digits reads as pixels.
       Only ``mode="visible"`` takes it.
   * - ``param``
     - ``"page"``
     - The query parameter that carries a raw token.
       ``restore`` is refused.
       A ``Window`` names its own parameter, so a ``param=`` that differs from it raises ``TemplateSyntaxError`` at render under ``DEBUG`` or ``STRICT_LOADING`` and yields to the window otherwise.
       A parameter other than ``page`` is written as ``data-next-param``.
   * - ``prune``
     - None
     - A row count from 1, written as ``data-next-prune``.
       Once the list holds more rows, the runtime drops whole batches from its top and points the ``<zone>-less`` link above the list at the batch right before the rows that remain.
       Only a ``next=`` tag takes it, and it needs a ``Window``.
       With a raw token the tag raises ``TemplateSyntaxError`` at render under ``DEBUG`` or ``STRICT_LOADING`` and renders the link without pruning otherwise, since a raw token names no batch for the link above the list.
   * - ``url``
     - None
     - ``replace`` or ``push``, written as ``data-next-url``.
       After each fetched batch the runtime writes ``?<param>=<token>&restore=1`` into the address bar with ``replaceState`` or ``pushState``, so a reload or a shared link restores the window.
       The write does not count as a page view.
       Only a ``next=`` tag takes it.

Option values are quoted literals, and ``margin`` and ``prune`` also take a bare integer.
An unknown option, a repeated one, or a value outside its set fails the template compile with a ``TemplateSyntaxError``.

The ``next_page`` filter returns ``next_page_number()`` of a Django :class:`~django.core.paginator.Page` that ``has_next()`` and ``None`` for anything else.
The ``previous_page`` filter returns ``previous_page_number()`` of a ``Page`` that ``has_previous()`` and ``None`` for anything else.
Both adapt a list paged by a Django :class:`~django.core.paginator.Paginator` to the raw-token form of the tag, which pages by ``?page=N`` and serves neither ``prune=`` nor a restored window.

Pagers
~~~~~~

A pager cuts the rows of a list into the window the query string of the request asks for.
It keeps no request state, so one module-level instance serves every render.
A context callable names the pager in an ``Annotated[Paging, <pager>]`` parameter and receives a ``Paging`` bound to the request.
``page.paginate(rows)`` returns the ``Window``, and ``await page.apaginate(rows)`` does the same from an ``async def`` callable, reading a queryset through its async API.
The template loops over the window and passes it to both tags.

.. code-block:: python
   :caption: feed/pages/feed/page.py

   from typing import Annotated

   from feed.models import Post

   from next import context
   from next.partial import KeysetPager, Paging, Window

   latest = KeysetPager(("-created", "-pk"), per_page=20)

   @context("rows")
   def rows(page: Annotated[Paging, latest]) -> Window[Post]:
       return page.paginate(Post.objects.filter(published=True))

.. code-block:: jinja
   :caption: feed/pages/feed/template.djx

   {% more zone="feed" previous=rows %}Show earlier{% endmore %}

   {% zone "feed" tag="ul" %}
     {% for post in rows %}
       <li data-next-key="{{ post.pk }}">{{ post.title }}</li>
     {% endfor %}
   {% endzone %}

   {% more zone="feed" next=rows prune=40 url="replace" %}Show more{% empty %}End of the feed.{% endmore %}

.. list-table::
   :header-rows: 1
   :widths: 22 22 56

   * - Pager
     - Query
     - Rows
   * - ``NumberPager(*, param="page", per_page=20, count=False)``
     - ``?page=N``
     - A queryset or a sequence in its own order, read in one slice that runs one row past the window.
   * - ``KeysetPager(ordering, *, param="cursor", per_page=20, count=False, max_token=512)``
     - ``?cursor=<token>``
     - A queryset, which the pager filters past the position of the token and orders itself, or a sequence of model instances already in the ordering, which it bisects without a query.

Both read one row past the window to learn whether a next batch follows, so neither counts the rows.
``count=True`` reads the total into ``window.count`` with one more query, for a header that shows it.
``param`` is a slug other than ``restore`` and ``per_page`` an integer from 1, and a pager built with anything else raises ``ValueError``.
A ``Paging`` annotation that names no pager, or more than one, raises ``TypeError`` when the callable is first resolved.
A render without a request reads the first window.

A ``NumberPager`` reads its page as follows.

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Query
     - Window
   * - No page, or a value that is not ASCII digits naming a page from 1 that a database offset can address
     - Page 1.
   * - ``?page=N``
     - Page ``N`` alone, and an empty window with no next and no previous batch when ``N`` lies past the last row.
   * - ``?page=N&restore=1``
     - Pages ``max(1, N - MAX_RESTORE + 1)`` to ``N``, ending on the last page that holds a row.

A ``KeysetPager`` pages by the position of a row under a total ordering.
The ordering is a sequence of field names, a leading ``-`` for descending, as in ``("-created", "-pk")``.
Every name is a local non-null column of the model, a foreign key by its attname as in ``author_id``, and the last one is unique, usually ``pk``, so no two rows share a position.
An ordering that is a bare string, empty, or names a column twice raises ``ValueError`` when the pager is built.
An ordering the model cannot page by raises :exc:`~django.core.exceptions.ImproperlyConfigured` on the first window read from that model.
The pager applies the ordering itself with ``order_by()``, so the ordering of the queryset it receives is not read.
A sequence whose rows are not model instances raises ``TypeError``.

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Query
     - Window
   * - No cursor, or a token the pager refuses
     - The first batch.
   * - ``?cursor=<token>`` from ``next``
     - The batch after the position of the token.
   * - ``?cursor=<token>`` from ``previous``
     - The batch before the position of the token.
   * - ``?cursor=<token>&restore=1``
     - The batch the token names with the whole batches before it, ``MAX_RESTORE`` batches at most.

A token holds a direction and the values of the ordering columns of one row, written as unpadded base64url of compact JSON.
It is neither signed nor encrypted, so a visitor can read those values and write a position of their own.
Such a position moves the window within the rows the queryset already serves and reaches no other row.
Each value is converted and validated by its model field before it reaches the query, and a datetime carries a time zone exactly when ``USE_TZ`` is on.
A token that is malformed, longer than ``max_token`` characters, or holds a value its column refuses reads the first batch instead of raising.
A row whose token would run past ``max_token`` raises ``ValueError`` when the window is built.
``encode(values, *, reverse)`` and ``decode(token)`` are the two methods a subclass overrides to sign or shorten tokens, and a ``Pager`` subclass that implements ``window`` and ``awindow`` pages by a scheme of its own, see :doc:`/content/howto/page-a-list-by-cursor`.

Windows
~~~~~~~

A ``Window`` is the frozen value a pager returns.
It iterates over its rows and has their length, so a template loops over the window itself and an empty window is falsy.

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Field
     - Meaning
   * - ``rows``
     - The rows this render shows.
   * - ``param``
     - The query parameter the tokens travel in.
   * - ``next``
     - The token of the batch after the rows, ``None`` at the end of the list.
   * - ``previous``
     - The token of the batch before the rows, ``None`` on the first window.
   * - ``back``
     - The token of the batch right before the one ``next`` fetches, which is the last batch of the window, ``None`` at the end of the list.
       Written as ``data-next-back`` on a link with ``prune=`` or ``url=``.
   * - ``backs``
     - The same token for every batch of a restored window after its first, in order.
       Written space-separated as ``data-next-backs`` on a link with ``prune=`` or ``url=``.
   * - ``count``
     - The total of the rows when the pager counts, ``None`` otherwise.

The runtime stamps every row of a list whose link sets ``prune=`` or ``url=`` with ``data-next-batch`` and keeps, for each batch, the token of the batch before it.
A fetched batch takes the ``data-next-back`` of the link that fetched it.
The rows a restored window renders are split evenly across its batches and take the tokens of ``data-next-backs``, so the first prune after a restore drops the oldest batch alone.
A prune drops whole batches from the top, never the last one, and writes the token kept for the first batch that remains into the ``<zone>-less`` link.

Restored windows
~~~~~~~~~~~~~~~~

``url=`` writes ``?<param>=<token>&restore=1`` after each fetched batch, with the token that fetched it.
A request for that address renders the batch the token names together with the batches before it, ``DEFER["MORE"]["MAX_RESTORE"]`` batches at most, ten by default, so a long scroll costs a bounded read.
Only the value ``1`` flags a restore, and both tags drop ``restore`` from the links they render.

A ``NumberPager`` reads the restored pages in its one slice.
A ``KeysetPager`` reads the batch after the position and the rows before it in two bounded queries.
While a next batch follows, it keeps whole batches only, so every batch boundary matches a back token, and a remainder short of a batch stays out of the window for the ``previous`` token to link.
At the end of the list it keeps every row it read.
A restored window that starts below the top of the list is grown back by the ``previous=`` tag above it, while the ``next=`` tag below keeps linking the batch after it.

``next.E235`` reports a ``{% more %}`` with ``prune=`` or ``url=`` on a page where no context callable a render runs, its own or one it inherits, takes an ``Annotated[Paging, <pager>]``, since the written restore address would render a single batch.
``next.E244`` reports two distinct pagers of one page that read the same query parameter, since each list would page by the token of the other.

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
     - The zone address, written by the ``{% zone %}`` and ``{% more %}`` tags.
   * - ``data-next-defer``
     - Deferred component wrapper, infinite-scroll sentinel
     - ``load``, ``visible`` or ``manual``, the delivery mode of a component that renders on its own later request.
       ``load`` fetches on ``ready``, ``visible`` waits for the viewport, and ``manual`` waits for a ``data-next-fetch`` control inside its loading content.
       On a pagination sentinel the value is not read, and the attribute only marks the link the observer watches for the merge GET.
       A dev build warns about an unrecognised value in either place.
   * - ``data-next-poll``
     - Zone wrapper
     - The poll interval in milliseconds, from the ``poll=`` literal, written on the full render and on the partial response wrapper.
       The runtime re-GETs the zone on the interval while the tab is visible.
       A hand-written value outside the whole-millisecond grammar, below the one-second floor, or above the browser timer ceiling is dropped, as is the attribute on an element without ``data-next-zone``, each with a console warning in dev.
   * - ``data-next-reload``
     - Zone wrapper
     - ``loading``, written by ``reload="loading"`` and left out for the default ``keep``.
       While a request reloads the zone, the runtime shows loading content after the delay instead of keeping the current content busy.
   * - ``data-next-delay``
     - Zone wrapper
     - The milliseconds a ``reload="loading"`` zone waits before it shows loading content, written from ``delay=`` when it differs from the default of 150.
   * - ``data-next-loading``
     - ``<template>``
     - The zone name or the boundary key whose loading content the template holds, written once per name and response on a page whose zone uses ``reload="loading"``.
       A zone's template holds its ``loading.<zone>.djx``, a boundary's template holds the ``loading.djx`` of a ``defer="inline"`` component inside such a zone.
   * - ``data-next-action``
     - ``<form>``
     - The action uid, written by ``{% form %}``, enables submit interception.
   * - ``data-next-transport``
     - ``<form>``
     - ``any``, written by ``{% form %}`` for an action declared ``transport="any"``.
       The submission and the inline validation of the form may then ride the open WebSocket, see :ref:`topics-forms-actions-transport`.
   * - ``data-next-validate``
     - ``<form>``
     - Inline validation on blur, written from the ``validate=`` tag parameter.
       The runtime keys off the presence of the attribute alone and never reads its value, so every value behaves the same and there is no closed set to choose from.
       The examples write ``blur`` because blur is the trigger.
   * - ``data-next-target``
     - ``<a>``, ``<form>``
     - Route the response into a zone.
       On a GET filter it names the zone to morph, and on a POST form it is written by the ``zone=`` tag parameter and travels as the morph target of the submission.
       On a ``{% more %}`` link it names the list zone and the zone of the link.
       A link with ``data-next-target`` and no ``data-next-merge`` stays a plain link, which the client router navigates like any other.
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
       Written by ``{% more %}`` from its ``merge=`` option.
       Any other value is ignored, with a console warning in dev.
   * - ``data-next-margin``
     - Infinite-scroll sentinel
     - The observer root margin, one to four ``px`` or ``%`` lengths, written by ``{% more %}`` from its ``margin=`` option.
       A value outside that grammar falls back to no margin.
   * - ``data-next-param``
     - Pagination link
     - The query parameter of the token, written by ``{% more %}`` when it is not ``page``.
   * - ``data-next-prune``
     - Pagination link
     - The row count above which the runtime drops whole batches from the top of the list, written by ``{% more %}`` from its ``prune=`` option.
   * - ``data-next-back``
     - Pagination link
     - The token of the batch right before the one the link fetches, written by ``{% more %}`` from the ``back`` of a ``Window`` on a link with ``prune=`` or ``url=``.
       The runtime keeps it with the rows the link fetches, for the link above the list after a prune.
   * - ``data-next-backs``
     - Pagination link
     - The same token for every batch of a restored window after its first, space-separated, written by ``{% more %}`` from the ``backs`` of a ``Window``.
   * - ``data-next-url``
     - Pagination link
     - ``replace`` or ``push``, how the runtime writes ``?<param>=<token>&restore=1`` after each fetched batch, written by ``{% more %}`` from its ``url=`` option.
   * - ``data-next-batch``
     - List rows
     - Written by the runtime on every row of a list whose link sets ``prune=`` or ``url=``, the batch the row arrived in, read back to drop whole batches.
   * - ``data-next-layer``
     - ``<a>``
     - Open the href in a layer, the zone container is created before the request.
   * - ``data-next-accepted``
     - The opening ``<a>``
     - A comma-separated list of zones the runtime re-fetches from the host page in one zone GET once the layer is accepted.
   * - ``data-next-confirm``
     - Form, link
     - A confirmation prompt before the request.
   * - ``data-next-keep``
     - Any element
     - The morph leaves the node untouched, paired by id when present and by position otherwise.
   * - ``data-next-key``
     - List rows, repeated ``<form>``, ``{% more %}`` sentinel
     - The match key for the morph and for ``append`` dedup, falling back to ``id``.
       On a repeated form it names the instance, written by the ``key=`` tag parameter, so a partial morph lands on the submitted form rather than the first.
       On a ``{% more %}`` sentinel it is the name of the zone that holds it, so a merge replaces the spent link.
   * - ``data-next-consented``
     - ``<template>``
     - The consent category whose grant reveals the inert body, written by ``{% #consented %}`` when the runtime decides consent, see :doc:`/content/topics/scripts/consent`.
   * - ``data-next-script``
     - ``<script>``
     - The name of a ``scripts.py`` script, written by the server on every script tag it renders, so the runtime never inserts the same script twice, see :doc:`/content/topics/scripts/declaring`.
   * - ``data-next-stream``
     - A container
     - Subscribe to a patch stream at the URL, over WebSocket when the server enables it and over ``EventSource`` otherwise.
   * - ``data-next-busy``
     - Layer opener, layer zone container
     - Written during a layer open on the opener link and the layer's zone container, alongside ``aria-busy="true"``.
       The submit double-click guard is the per-uid mutation lock, not this attribute.
   * - ``data-next-dialog``
     - Runtime ``<dialog>``
     - Set by the runtime on every layer dialog, the styling hook for the modal shell.
   * - ``data-next-toasts``
     - A container in the page, or the runtime's own
     - The toast tray and the styling hook for the stack, found top layer down so a tray in the layout or in a modal receives every toast.
       Only a tray the runtime builds itself, on the first ``toast`` of a page that renders none, gets ``aria-live="polite"``, so a hand-written tray carries its own live region.
   * - ``data-next-toast``
     - Runtime toast item
     - One toast, the value is the variant, the styling hook for a single notification.
       The text is set as ``textContent`` and never parsed as HTML, so a toast string cannot carry markup.

.. warning::

   ``data-next-validate`` carries no off switch, unlike the closed value sets of ``data-next-defer`` and ``data-next-merge``.
   Writing ``validate="off"`` on the tag turns inline validation on, because the rendered attribute is present either way.
   Omit the ``validate=`` parameter to leave inline validation off.

Lifecycle events
----------------

The runtime fires its events on three channels, the element, the document, and the ``Next.on`` bus.
Every ``partial:*`` event and every ``next:*`` runtime event fires on the document and the bus with the same payload, while ``ready`` and ``context-updated`` reach only the bus.
The ``next:mounted``, ``next:removed``, and ``next:morph-*`` node events fire on the element as a bubbling ``CustomEvent`` and never reach the bus.
See :ref:`ref-client` for every event and its payload.

``partial:error`` is a discriminated union on ``kind``, and each cause carries only its own fields, so a listener branches on ``kind`` before reading them.
An ``AbortError`` never reaches this event.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Kind
     - Cause
   * - ``{kind: "network", error, url?}``
     - A fetch reject, a response body whose read fails after the headers arrived, a header name the browser refuses, a dropped stream connection, a ``next-end`` denial, whose code is the ``error``, a zone that still answers a non-envelope after the navigate-once fallback already navigated, a target off the page's origin refused unsent, or a request past its deadline, whose ``error`` is a ``TimeoutError``.
       ``url`` is present only on those last two.
   * - ``{kind: "http", status, body}``
     - A 5xx, a mutating reply that is not an envelope, or a 4xx a safe GET got without an envelope before the runtime navigates.
   * - ``{kind: "parse", body, error}``
     - A malformed JSON body, a JSON body that is not an envelope, or a body a parse hook rejects.
   * - ``{kind: "op", op, error, target?}``
     - A thrown or unknown verb mid-apply, where ``op`` names the verb and ``target`` is the human-readable address of the patch, present only when the op carried a recognised target.
   * - ``{kind: "asset", error, url?}``
     - A stylesheet that failed to load, a version mismatch surviving a reload, a reload target off the page's origin, or a runtime chunk that failed to load, where ``url`` is present on all but the first.
   * - ``{kind: "csrf", error, url?}``
     - A deferred CSRF token the runtime could not fetch, where ``url`` names the mutation that never left.
   * - ``{kind: "undelivered", url, reason}``
     - A mutation the WebSocket tunnel ran or may have run whose reply never reached the page, where ``reason`` is the refusal code, ``closed`` for a dropped socket, or ``timeout`` for a wait that ran out.
       The page re-reads its state rather than resending, and on ``http_only`` the runtime reloads it once the listeners ran, see :ref:`topics-realtime-tunnel`.

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
     - The heartbeat period of a ``PatchEventStream`` with an async source and of every ``TopicStream``, ``None`` or a finite number of seconds above ``0``.
   * - ``SSE.RETRY_MS``
     - ``3000``
     - The ``EventSource`` reconnect hint, ``None`` or an integer of milliseconds from ``0`` that is not a bool, where ``0`` asks the browser to reconnect at once.

``SSE`` itself is a dict or ``None``, and ``None`` in either option takes the default.
A wrong value falls back to the default at runtime, and ``manage.py check`` reports it as ``next.E214``.

See :doc:`/content/ref/settings` for every key inside ``NEXT_FRAMEWORK``.

See also
--------

.. seealso::

   :doc:`scenarios` for each verb, header, and attribute in the context of a task.
   :doc:`/content/ref/client` for the ``window.Next`` surface that applies every envelope.
   :doc:`layers` for the modal narrative behind the layer verbs and attributes.
   :doc:`/content/ref/system-checks` for the zone and verb checks.
   :doc:`/content/topics/signals` for the partial subsystem signals.
