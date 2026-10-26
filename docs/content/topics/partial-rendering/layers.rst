.. _topics-partial-rendering-layers:

Layers and modals
=================

A layer is a modal the runtime opens over the current view and fills with a zone of a page.
A ``data-next-layer`` link opens one from the markup, ``Patches.layer_open`` opens one from a handler, and both load the same thing, a named zone of a page that also renders on its own.

.. contents::
   :local:
   :depth: 1

Intercepting modals
-------------------

A ``data-next-layer`` link opens a modal over the current view and pushes the real URL of the modal body.
The pushed URL is the real address of the body rather than a masked URL of the page beneath it.
A refresh or a shared link resolves that URL as its own standalone page through its own ``page.py``, and Back closes the top layer.
There is no client router and no URL masking.

A single ``popstate`` handler closes the layer whose pushed URL the browser moved past.
A layer whose push the browser refused has no history entry, so Back leaves it open.
The URL is pushed as the modal opens, so Back closes it even while its body is still loading.
A layer whose body fails to load, or that closes before the body arrives, replaces the URL back to the page underneath.

``data-next-confirm`` and ``data-next-layer`` combine on one link.
The confirm gate is a capture-phase click handler, the layer opener is a bubble-phase one, so the confirm runs first regardless of install order.
A cancelled confirm stops the click before it reaches the opener, so the layer never opens.
An accepted confirm lets the click through and the layer opens.
The same gate protects every click-driven trigger, so a prompt fronts a layer open the same way it fronts a pagination merge.

Opening another page's zone over its own URL
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An intercepting route lets a link open a different page in the modal while the address bar shows the honest URL of the link target, see :ref:`topics-file-router-intercepts` for the directory layout.
The opt-in stays the link.
``<a href="/photo/7/" data-next-layer="photo-modal">`` sends a zone GET of ``/photo/7/`` with ``X-Next-Zone: photo-modal``, and the zone name selects which page renders it.
The client needs no extra attribute, and the zone name on ``data-next-layer`` stays mandatory.
The markers ``(.)``, ``(..)``, and ``(...)`` of the directory name only the target and give the intercept its inherited layouts, context, access, and components.
They do not restrict where the modal opens from, so any page can link with that zone.

The selection runs on a GET or a HEAD only and follows two rules.
The target keeps every zone it declares as a standalone zone itself.
Otherwise the first intercept of the target, in file order, whose page declares every requested zone as a standalone zone serves the request.
The zone sets come from the compiled template of each page, so the selection renders nothing.
A request names the zones of one page only.
A set that mixes a zone of the target with a zone of an intercept is served by no intercept, so the target renders the zones it declares and a server-side render of the intercept zone raises ``UnknownZoneError``.
A target without intercepts pays one dictionary lookup.

The URL stays the real address of the target.
A reload renders the real target page with its own head, Back closes the layer, and a plain link without ``data-next-layer`` navigates to the target page.

``Patches.layer_open(href="/photo/7/", zone="photo-modal")`` makes the client issue the same GET, so it selects the intercept the same way.

The modal title comes from the intercept page.
The zone response carries one ``meta`` patch with the folded metadata of the intercept page, the title, the description, the canonical link, and the robots meta, and a key the page leaves out removes its tag.
The request is the honest target GET, so a self canonical names the target URL.
The layer restores the host head on close, see `Titles under a layer`_.
A metadata callable that fails is logged, the patch is left out, and the zone still answers 200.
A zone GET served by the target itself carries no ``meta`` patch.

A request from inside the modal runs the same selection and both access checks every time.
A refresh, a poll, a lazy zone, a stream, and a pagination link GET the layer URL, or the href of the pagination link, with ``X-Next-Zone`` only.
A pagination href is therefore rendered on the target path, and a query string is fine.
A nested zone of the intercept page is selected like its outer zone.
The target and the intercept each climb their own file-system ancestors for access, and the stricter chain wins.
An object permission on the intercept reads the URL kwargs of the target.

Forms and actions in the modal
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A form posts with the ``X-Next-Zone`` of the intercept zone.
The server resolves the served page from the posted origin and that zone, so an invalid form re-renders the intercept zone, and an action that returns ``None`` morphs it.
``Patches.morph_zone(zone)`` on such a request renders an intercept zone the origin page lacks, after the intercept page authorizes the request.
A POST never selects an intercept on the page view, so no mutation reaches an intercept through it.
An intercept that refuses on the form or action path answers 403, also for a login rule and for an ``Http404``, since that path has no redirect and the action may have run.
A broken chain of intercepts answers 403 too, except for the import error it shows under ``DEBUG``.
``Patches.morph_foreign_zone`` does not consult intercepts and raises ``UnknownZoneError`` for an intercept zone.
The action owner and its guard protect a mutation, the modal does not.
A wizard step inside a modal posts the same way, and :doc:`done-choreographies` compares the two ways its last step refreshes the page beneath.

Response order of a zone GET
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A zone GET of a target with intercepts answers with the first step that applies.

#. The access chain of the target refuses, and its response is the answer.
#. The ``render()`` of a target that resolves a body runs, and an ``HttpResponse`` it returns is the answer.
#. The intercept is picked.
#. The access chain of the intercept refuses, and its response is the answer with no fallback to the target.
   A 403, a 404, or a login redirect arrives carrying ``private, no-store`` and the partial ``Vary``, since the same URL serves the target in full.
   The project ``handler403`` and ``handler404`` render the 403 and the 404 inside the page view, so ``process_exception`` of a middleware never sees them, and ``intercept_access_denied`` announces each refusal instead, see :doc:`/content/ref/signals`.
#. A dynamic body answers 400, only when no intercept serves.
#. A stale asset version answers 409.
#. The zone renders over the intercept page, and a zone the page does not declare answers 400.

The browser follows a login redirect with the zone headers kept, so the login page answers 400 for a zone it does not declare, and the runtime then navigates to the login page in full.

Zone names are public markup and no secret.
A requester who passes the target sees a 403 or a login redirect for a real intercept zone and a 400 for an unknown one.
A ``render()`` that returns a soft-denial string does not stop an intercept, which still serves.
Deny by raising ``PermissionDenied`` or ``Http404``, or by returning an ``HttpResponse``.
The intercept declares its own access, and a check reports one that leaves a public target's modal behind the default login, see :doc:`/content/ref/system-checks`.
:doc:`/content/howto/open-a-page-as-a-modal-over-another` walks the whole setup.

Titles under a layer
--------------------

Every layer keeps the head underneath it, the title, the description, the canonical link, and the robots meta.
It restores them when it closes, whichever way it closes, by accept, dismiss, Back, or reset.
A ``meta`` patch syncs the head of the page whose envelope carried it.

Which page a title belongs to decides how long it lasts.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Title carried by
     - Effect
   * - The layer's own page, through the layer body, a zone GET inside the layer, or a mutation fired from it.
       The ``meta`` patch of an intercept page is included, see `Opening another page's zone over its own URL`_.
     - Lasts as long as the layer.
   * - The host page or a lower layer, through a poll of the base page, a lazy zone GET, or a stream the host subscribed.
     - Becomes the title the covering layer restores, so the newest title of the page underneath survives the close.
       It shows at once when the covering layers set no title of their own.
       Otherwise the layer title stays in the tab until they close.

With nested layers each one restores the newest title of the layer below it.

A mutation counts as the page its form sits in.
That is the host page for a form outside every layer or inside a layer that pushed no URL, and the layer's own page for a form inside a layer that did.
A programmatic ``Next.partial.apply()`` carries no page, so its title counts as the top layer's.
A stream counts as the page that subscribed it only while that URL still matches the host or the pushed URL of a layer.
Otherwise its title counts as the top layer's as well.

.. _partial-server-layers:

Server-initiated layers
-----------------------

``Patches.layer_open`` opens a layer from a handler, the server counterpart of the ``data-next-layer`` opener.
Its signature is ``layer_open(*, zone=None, href=None)``, and the two keywords select one of three forms.

A layer shows a zone of a page, uniformly.
There is no separate mechanism for a whole page in a modal.
A page that opens in a layer declares a zone with ``{% zone "name" %}``.
That name travels to ``layer_open`` or to ``data-next-layer``.

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
The href is validated same-site like every navigation sink.
A cross-site value raises ``CrossSiteHrefError``.

The client ``data-next-layer="record"`` opener and the server ``layer_open(href, zone)`` do the same work.
Both load a page zone into a layer.

Foreign-zone authorization
--------------------------

The layer runtime sends ``X-Next-Origin`` with the host page on the modal body fetch, the accepted re-GET, and a mutation fired inside the layer, and a handler reads it back through ``resolve_partial_origin`` to address a zone of that page with ``morph(page=, url_kwargs=)``.
``morph(zone=)`` always renders against the posted form origin and ignores the header.
The server authorizes the page behind every zone render whose own view did not run, the one the handler names and the one the posted form origin names alike, raising ``ForeignPageNotAuthorizedError`` when that page may not be rendered for the requester.
This keeps a page-addressed render in a response from reaching a zone the requester has no claim on.
A foreign page whose module fails to import, or that sits under a ``page.py`` that fails to import, raises ``PageModuleImportError`` from the same authorization step in production too, rather than turning the in-flight request into a 404 and dropping the patches already queued for it.

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

The Tailwind ``@apply`` block is an alternative for a project that already runs Tailwind.

See also
--------

.. seealso::

   :doc:`scenarios` for the modal wizard walked from markup to handler.
   :doc:`reference` for the layer verbs, attributes, and events in tables.
