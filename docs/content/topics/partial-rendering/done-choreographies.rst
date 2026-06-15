.. _topics-partial-rendering-done-choreographies:

Wizard Done Choreographies
==========================

A wizard inside a modal finishes on its last step.
The request is created, the modal closes, and the list on the page beneath it has to refresh.
There are two ways to drive that refresh.
This page describes both and compares them honestly so the choice is informed.

The opening and the steps are identical for both, see :doc:`scenarios`.
The two choreographies differ only in what ``done`` returns and how the list gets fresh data.

.. contents::
   :local:
   :depth: 1

Accept and Re-GET
-----------------

The default.
The wizard closes the layer with a result, and the link that opened the modal re-fetches the zone it named in ``data-next-accepted``.
The wizard does not know the list exists.

.. code-block:: python
   :caption: request/[step]/page.py

   def done(self, request: HttpRequest, cleaned_data: dict[str, Any]) -> PatchResponse:
       """Create the access request and close the layer with a result."""
       access_request = AccessRequest.objects.create(**cleaned_data)
       return (
           Patches(request)
           .layer_close(result={"id": access_request.pk})
           .toast("Request created", variant="success")
           .response(fallback=f"/request/{access_request.pk}/audit/")
       )

The last step answers with a close and a toast.

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "layer.close", "result": {"id": 42}},
       {"op": "toast", "text": "Request created", "variant": "success"}
     ]
   }

The runtime closes the layer, fires accept, and by ``data-next-accepted="request-list"`` sends a second GET for the list.

.. code-block:: http
   :caption: the re-GET

   GET / HTTP/1.1
   X-Next-Request: 1
   X-Next-Zone: request-list

The list re-renders through its own page view, with its own guards and middleware, on a request that carries the caller's cookies.
The wizard is portable: it sits on any page without a change, and ``data-next-accepted`` moves with the markup.

This is the cycle of a redirect with reloaded props.
The cost is one extra GET after the modal closes, which the dialog animation hides.

Server OOB Through Page Addressing
----------------------------------

The alternative.
The wizard puts the morph of the list's zone into its own response, so one envelope closes the layer, refreshes the list, and shows the toast.

This addresses a zone of a foreign page.
The builder takes ``page=`` and ``url_kwargs=`` alongside ``zone=``, and the request carries ``X-Next-Origin`` with the path of the page that hosts the layer.

.. code-block:: python
   :caption: request/[step]/page.py

   from next.partial import resolve_partial_origin


   def done(self, request: HttpRequest, cleaned_data: dict[str, Any]) -> PatchResponse:
       """Create the access request and patch the host page list in one response."""
       access_request = AccessRequest.objects.create(**cleaned_data)
       origin = resolve_partial_origin(request)
       return (
           Patches(request)
           .layer_close(result={"id": access_request.pk})
           .morph(zone="request-list", page=origin.page_path, url_kwargs=origin.url_kwargs)
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
The page's guards and redirects are honoured, so the list never travels in the response when the page would have denied the caller on its own request.
A denial surfaces as ``ForeignPageNotAuthorizedError`` rather than an empty morph.

This is the cycle of an out-of-band swap.
One round trip closes everything at once.

The Comparison
--------------

.. list-table::
   :header-rows: 1
   :widths: 26 37 37

   * - Dimension
     - Accept and re-GET
     - Server OOB through ``page=``
   * - Round trips after the last step
     - Two: the close, then the list GET
     - One
   * - Coupling
     - ``done`` does not know the list, the opening link binds them
     - ``done`` names the zone of a foreign page
   * - List authorization
     - The list's own view, on a request with its cookies and middleware
     - The shaping step re-runs the host page's body resolution
   * - Protocol surface
     - No new headers or addressing
     - Adds ``X-Next-Origin`` and the ``page=`` addressing of the builder
   * - Wizard reuse
     - Drops onto any page, ``data-next-accepted`` moves with it
     - ``done`` hard-codes the zone or branches on the origin
   * - UX atomicity
     - A one-GET gap between close and list, masked by ``aria-busy``
     - Close, list, and toast apply in one envelope
   * - Without the runtime
     - Identical: a 303 to ``fallback``
     - Identical

The recommendation is accept and re-GET as the default.
The deciding argument is not the round trip but the authorization and the decoupling.
The re-GET keeps the list's rights where they live, in the list's view, and leaves the wizard portable.
Reach for server OOB when the one extra GET is genuinely visible and the wizard is dedicated to one host page.

See Also
--------

.. seealso::

   :doc:`scenarios` for the full modal wizard scenario.
   :doc:`reference` for the ``layer.close`` and ``morph`` verbs and the ``X-Next-Origin`` header.
