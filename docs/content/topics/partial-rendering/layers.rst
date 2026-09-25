.. _topics-partial-rendering-layers:

Layers and modals
=================

A layer is a modal the runtime opens over the current view and fills with a zone of a page.
A ``data-next-layer`` link opens one from the markup, ``Patches.layer_open`` opens one from a handler, and both load the same thing, a named zone of a page that also renders on its own.
This page covers the click-side interception, the server builder, the authorization step a foreign page's zone passes through, and the CSS hooks the runtime leaves behind.

.. contents::
   :local:
   :depth: 1

Intercepting modals
-------------------

A ``data-next-layer`` link opens a modal over the current view and pushes the honest URL of the modal body.
The pushed URL is the real address of the body rather than a masked URL of the page beneath it.
A refresh or a shared link resolves that URL as its own standalone page through its own ``page.py``, and Back closes the top layer.
There is no client router and no URL masking.
A single ``popstate`` handler closes the layer whose pushed URL the browser moved past.
Every layer captures ``document.title`` when it opens and restores it when it closes, whichever way it closes, so a ``meta`` patch received inside the layer retitles the tab only for as long as the layer is up.

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
     - Open a bare modal shell.
       Its container carries no zone name, so only a css-targeted patch can address it.
       Name a zone to fill the modal with zone patches.
   * - ``layer_open(zone="cart")``
     - Open a layer whose zone container is named ``cart``, so a following ``morph(zone="cart")`` in the same envelope lands inside the modal.
   * - ``layer_open(href="/records/42/", zone="record")``
     - Fetch the ``record`` zone of ``/records/42/`` and load it into the layer.
       The page at that href declares ``{% zone "record" %}``.

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

Foreign-zone authorization
--------------------------

A modal body and a page-addressed zone ride ``X-Next-Origin`` so the server resolves the host page that owns the zone.
The server authorizes the page behind every zone render whose own view did not run, the one the handler names and the one the origin names alike, raising ``ForeignPageNotAuthorizedError`` when that page may not be rendered for the requester.
This keeps a page-addressed out-of-band render from reaching a zone the requester has no claim on.
A foreign page whose module fails to import raises ``PageModuleImportError`` from the same authorization step, rather than turning the in-flight request into a 404 and dropping the patches already queued for it.

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
   [data-next-toast="warning"] { /* warning variant */ }
   [data-next-toast="error"] { /* error variant */ }

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

The shared ``examples/_shared/static/shared/css/base.css`` file uses only the plain-CSS pattern shown above.
The Tailwind ``@apply`` block is an alternative for a project that already runs Tailwind.

See also
--------

.. seealso::

   :doc:`scenarios` for the modal wizard walked from markup to handler.
   :doc:`reference` for the layer verbs, attributes, and events in tables.
