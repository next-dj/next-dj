.. _topics-partial-rendering-done-choreographies:

Wizard done choreographies
==========================

A wizard inside a modal finishes on its last step.
The request is created, the modal closes, and the list on the page beneath it has to refresh.
There are two ways to drive that refresh.
This page describes both and compares them so the choice is informed.

The opening and the steps are identical for both, see :doc:`scenarios`.
The two choreographies differ only in what ``done`` returns and how the list gets fresh data.

.. contents::
   :local:
   :depth: 1

Accept and re-GET
-----------------

This is the default.
The wizard closes the layer with a result, and the link that opened the modal re-fetches the zone it named in ``data-next-accepted``.
The wizard does not know the list exists.

The default choreography is the one :doc:`scenarios` walks through, from the ``done`` method that returns a ``layer.close`` and a toast to the re-GET the accepted zone earns.

The list re-renders through its own page view, with its own guards and middleware, on a request that carries the caller's cookies.
The wizard is portable.
It sits on any page without a change, and ``data-next-accepted`` moves with the markup.

The cost is one extra GET after the modal closes.

The result a ``layer_close(result=...)`` carries reaches the page twice.
The runtime fires ``partial:layer-accepted`` with the result, and the ``Outcome.envelope`` of the request that carried the close holds the same envelope, see :doc:`/content/ref/client`.

Page-addressed morph in the response
------------------------------------

The wizard puts the morph of the list's zone into its own response, so one envelope closes the layer, refreshes the list, and shows the toast.

This addresses a zone of a foreign page through ``morph(page=, url_kwargs=)``, the one path the server takes to refresh a host-page zone from a ``done`` step.
The builder takes ``page=`` and ``url_kwargs=`` alongside ``zone=``, and the request carries ``X-Next-Origin`` with the path and query string of the page that hosts the layer.
``resolve_partial_origin`` reads that header back into the ``page_path`` and ``url_kwargs`` the morph needs.

.. code-block:: python
   :caption: request/[step]/page.py

   from typing import Any

   from access.models import AccessRequest
   from django.http import Http404, HttpRequest, HttpResponse

   from next.forms import FormWizard
   from next.partial import Patches, resolve_partial_origin

   class AccessRequestWizard(FormWizard):
       def done(
           self, request: HttpRequest, cleaned_data: dict[str, Any]
       ) -> HttpResponse:
           """Create the access request and patch the host page list in one response."""
           access_request = AccessRequest.objects.create(**cleaned_data)
           origin = resolve_partial_origin(request)
           if origin is None or origin.page_path is None:
               raise Http404
           return (
               Patches(request)
               .layer_close(result={"id": access_request.pk})
               .morph(
                   zone="request-list",
                   page=origin.page_path,
                   url_kwargs=origin.url_kwargs,
               )
               .toast("Request created", variant="success")
               .response(fallback=f"/request/{access_request.pk}/audit/")
           )

The last step answers with all three operations in one envelope.

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "layer.close", "result": {"id": 42}},
       {"op": "morph", "target": {"zone": "request-list"},
        "html": "<div data-next-zone=\"request-list\">…</div>"},
       {"op": "toast", "text": "Request created", "variant": "success"}
     ]
   }

A ``morph(page=…)`` re-runs the foreign page's body resolution before rendering its zone.
A ``morph(zone=…)`` against the posted origin re-runs the body resolution of that page the same way.
The page's guards and redirects are honoured either way, so the list never travels in the response when the page would have denied the caller on its own request.
A denial surfaces as ``ForeignPageNotAuthorizedError`` rather than an empty morph.
A foreign page whose body resolves dynamically raises ``DynamicForeignPageError``, since it has no template to render the zone from.

One round trip closes everything at once.

``morph(page=)`` emits no ``context`` patch, while ``morph(zone=)`` against the origin page does whenever the zone has values to ship.
``Next.context`` therefore keeps the values it held for the host page after a page-addressed morph.
An explicit ``.context(...)`` names providers that are validated against the origin page, so it fits when those providers belong to the origin.
Otherwise the page re-GETs the zone, and the zone GET ships the context patch.

The comparison
--------------

.. list-table::
   :header-rows: 1
   :widths: 26 37 37

   * - Dimension
     - Accept and re-GET
     - Page-addressed morph through ``page=``
   * - Round trips after the last step
     - Two, the close and then the list GET
     - One
   * - Coupling
     - ``done`` does not know the list, the opening link binds them
     - ``done`` names the zone of a foreign page
   * - List authorization
     - The list's own view, on a request with its cookies and middleware
     - The ``morph(page=)`` call re-runs the host page's body resolution before its zone renders
   * - Protocol surface
     - No new headers or addressing
     - Adds ``X-Next-Origin`` and the ``page=`` addressing of the builder
   * - Wizard reuse
     - Drops onto any page, ``data-next-accepted`` moves with it
     - ``done`` hard-codes the zone or branches on the origin
   * - UX atomicity
     - A one-GET gap between close and list
     - Close, list, and toast apply in one envelope
   * - Without the runtime
     - A 303 to ``fallback``, and the list zone never renders because ``done`` builds no morph of it
     - A 303 to ``fallback`` too, but the response still renders the foreign zone into the envelope the redirect then discards

The recommendation is accept and re-GET as the default.
The deciding argument is not the round trip but the authorization and the decoupling.
The re-GET keeps the list's rights where they live, in the list's view, and leaves the wizard portable.
Reach for the page-addressed morph when the one extra GET is genuinely visible and the wizard is dedicated to one host page.

See also
--------

.. seealso::

   :doc:`scenarios` for the full modal wizard scenario.
   :doc:`reference` for the ``layer.close`` and ``morph`` verbs and the ``X-Next-Origin`` header.
