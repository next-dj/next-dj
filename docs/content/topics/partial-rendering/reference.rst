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
     - The default verb. Morph the target into the HTML. ``extract: true`` carries a whole document the client trims to the target.
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
     - —
     - Server-initiated open of a layer.
     - —
   * - ``layer.close``
     - ``layer_close()``
     - Close the top layer with an accept result or a dismissal.
     - accept, no result
   * - ``url``
     - ``push_url()``
     - Push or replace browser history. The href is validated by the server.
     - ``action: "push"``
   * - ``visit``
     - ``redirect()``
     - A full client navigation to a server-authored href.
     - —

A verb beyond this set is registered on both sides.
``register_patch_op("confetti")`` on the server clears the ``next.E066`` check and earns the generic ``op()`` channel on the builder.
``Next.partial.defineOp("confetti", handler)`` on the client supplies the handler.

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
     - Lazy zone wrapper
     - ``load`` or ``revealed``, the materialisation trigger.
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
     - Form, link
     - The triggering event: ``input``, ``change``, ``submit``, ``click``, ``revealed``, ``load``.
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
     - Any element with an id
     - The morph leaves the node untouched.
   * - ``data-next-key``
     - List rows
     - The match key for the morph and for ``append`` dedup, falling back to ``id``.
   * - ``data-next-sse``
     - A container
     - Subscribe to a patch stream at the URL.
   * - ``data-next-busy``
     - Initiator and target
     - The busy gate, written by the runtime alongside ``aria-busy="true"``.
   * - ``data-next-dialog``
     - Runtime ``<dialog>``
     - Set by the runtime on every layer dialog, the styling hook for the modal shell.

Lifecycle Events
----------------

The runtime fires events on the document and the ``Next.on`` bus.

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
     - Fired on each touched node, bubbles. The node is the event target.

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
               "DEFAULT_SWAP": "morph",
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
   * - ``DEFAULT_SWAP``
     - ``"morph"``
     - The default verb of a zone patch.
   * - ``VERSION``
     - ``"manifest"``
     - The source of ``X-Next-Version``. The sentinel hashes the staticfiles manifest when the active storage hashes its files, an explicit string overrides it, and without a manifest the version guard stays silent.
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
No framework CSS is applied — the selectors are the hook.

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
