.. _topics-partial-rendering-reference:

Partial Rendering Reference
===========================

The patch verbs, the request and response headers, the ``data-next-*`` attributes, and the ``PARTIAL_BACKENDS`` settings, in tables.
For the narrative behind any of these, read the scenario that uses it in :doc:`scenarios`.

.. contents::
   :local:
   :depth: 1

Patch Verbs
-----------

A patch is one addressed DOM operation with a verb, an optional target, optional HTML, and verb-specific extras.
The operations apply in list order.
The server is the only author of a target, the client never names one.

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
     - —
   * - ``inner``
     - ``inner()``
     - Replace only the contents, no morph.
     - —
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
     - —
   * - ``refresh``
     - ``refresh()``
     - Ask the client to re-fetch the zone with its own cookies. The safe default of an SSE fan-out.
     - —
   * - ``context``
     - ``context()``
     - Merge named serialize-provider values into ``Next.context`` and fire ``context-updated``.
     - —
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
     - Open a layer from the server, by zone or href.
     - —
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
     - —

A verb beyond this set is registered on both sides.
``register_patch_op("confetti")`` on the server clears the ``next.E066`` check and earns the generic ``op()`` channel on the builder.
``Next.partial.defineOp("confetti", handler)`` on the client supplies the handler.
See :doc:`extending` for the end-to-end recipe, the ``context`` and ``event`` seams, and the custom-verb exceptions.

Request Headers
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
     - Every intercepted request
     - The asset version the client holds.
   * - ``X-Next-Request-Id``
     - Every mutation
     - The ring id used to suppress an SSE echo.
   * - ``X-Next-Origin``
     - Server OOB choreography only
     - The path of the page that hosts a layer, for a server-side morph of its zones.
   * - CSRF header
     - Every unsafe method
     - The name comes from ``CSRF_HEADER_NAME``, the token from the runtime payload, the cookie is never read.

Response Headers
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
     - ``X-Next-Request, X-Next-Zone, X-Next-Merge``, set by the framework, so a shared cache is not poisoned.
   * - ``X-Next-Version``
     - Every envelope
     - The current asset version.
   * - ``X-Next-Form: invalid``
     - An invalid form
     - The existing invalid-form contract.
   * - ``X-Next-Action``
     - An invalid form
     - The uid of the failed action.

Status Codes
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
   * - 302 or 403 without an envelope
     - A guard denial or a CSRF failure served outside the shaping path. The runtime navigates fully.
   * - 400
     - An intent that did not validate: an unknown zone, a bad origin, a zone in a dynamic page body.
   * - 404
     - An unknown form uid, the existing behaviour.
   * - 409
     - A version mismatch on a safe method, with an empty body. The runtime fully visits the current URL. A mutation always runs and expresses a mismatch with a ``visit`` patch instead.
   * - 5xx
     - No envelope. The runtime swaps nothing and fires ``partial:error``.

Attributes
----------

The single namespace the runtime reads is ``data-next-*``.
The form-behaviour attributes are written by the ``{% form %}`` tag from Python parameters, not hand-authored as a string DSL.

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
       ``revealed`` arms the observer that fires the merge GET.
   * - ``data-next-action``
     - ``<form>``
     - The action uid, written by ``{% form %}``, enables submit interception.
   * - ``data-next-validate``
     - ``<form>``
     - Inline validation, source is the ``validate=`` tag parameter.
   * - ``data-next-target``
     - ``<a>``, GET ``<form>``
     - Route the response into a zone, source is the ``zone=`` tag parameter on a form.
   * - ``data-next-trigger``
     - Filter ``<form>``, sort ``<select>``
     - The event that auto-submits a GET filter, ``input`` or ``change``. Submit and click interception are wired by ``data-next-action`` and ``data-next-merge``, not this attribute.
   * - ``data-next-debounce``
     - With ``data-next-trigger``
     - Debounce in milliseconds.
   * - ``data-next-merge``
     - Pagination link
     - ``append`` or ``prepend``, travels as ``X-Next-Merge``.
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
     - Initiator and target
     - The busy gate, written by the runtime alongside ``aria-busy="true"``.
   * - ``data-next-dialog``
     - Runtime ``<dialog>``
     - Set by the runtime on every layer dialog, the styling hook for the modal shell.
   * - ``data-next-toasts``
     - Runtime toast container
     - The toast tray, created by the runtime on the first ``toast`` and the styling hook for the stack.
   * - ``data-next-toast``
     - Runtime toast item
     - One toast, the value is the variant, the styling hook for a single notification.

Lifecycle Events
----------------

The runtime fires events on the document and the ``Next.on`` bus.
The ``next:*`` node events fire on the element as a bubbling ``CustomEvent`` caught with ``addEventListener``.
The ``partial:*``, ``ready``, ``context-updated``, and ``next:toast`` events also reach the ``Next.on`` bus.

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
     - Read context through ``Next.context``.
   * - ``partial:before-request``
     - Yes
     - ``{url, method, intent}``
   * - ``partial:before-apply``
     - Yes
     - ``{envelope}``, the op list is mutable.
   * - ``partial:applied``
     - No
     - ``{envelope}``
   * - ``partial:error``
     - No
     - ``{status, body, error}``. An ``AbortError`` does not reach it.
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
     - Fired on a node just before it detaches, bubbles, no detail. The unmount half of the island lifecycle, the place to tear down a mounted root or a timer.
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

Client Runtime
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
   * - ``Next.partial.layers``
     - The layer stack for driving modals from script: ``open(opener, href, zone)``, ``close(detail)``, and ``size()``.
   * - ``Next.partial.sse``
     - The stream registry: ``size()`` for the count of open connections and ``remember(id)`` to feed a request id into the echo ring.

Intercepting Modals
-------------------

A ``data-next-layer`` link opens a modal over the current view and pushes the honest URL of the modal body.
The pushed URL is the real address of the body rather than a masked URL of the page beneath it.
A refresh or a shared link resolves that URL as its own standalone page through its own ``page.py``, and Back closes the top layer.
There is no client router and no URL masking.
A single ``popstate`` handler closes the layer whose pushed URL the browser moved past.

Foreign-Zone Authorisation
--------------------------

A modal body and a page-addressed zone ride ``X-Next-Origin`` so the server resolves the host page that owns the zone.
The server authorises that origin before rendering a foreign page's zone, raising ``ForeignPageNotAuthorizedError`` when the origin may not render it.
This keeps a page-addressed out-of-band render from reaching a zone the requester has no claim on.

Settings
--------

The partial subsystem reads ``PARTIAL_BACKENDS`` inside ``NEXT_FRAMEWORK``.
The list holds the protocol backends, with the first one active.

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

Styling Layers and Toasts
-------------------------

The runtime creates a bare ``<dialog data-next-dialog>`` for every layer and a ``<div data-next-toasts>`` container for toasts.
No framework CSS is applied. The selectors are the hook.

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

See Also
--------

.. seealso::

   :doc:`scenarios` for each verb, header, and attribute in the context of a task.
   :doc:`/content/ref/system-checks` for the zone and verb checks.
   :doc:`/content/topics/signals` for the partial subsystem signals.
