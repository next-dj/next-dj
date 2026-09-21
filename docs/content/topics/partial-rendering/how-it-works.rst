.. _topics-partial-rendering-how-it-works:

How a partial request flows
===========================

A partial update is one request and one response laid over the ordinary page cycle.
The server authors every DOM operation, the client applies it, and the wire carries verbs and addresses rather than selectors or swap strategies.
This page follows one update end to end, a catalogue page narrowing its result list as the user types.

.. contents::
   :local:
   :depth: 1

The zone in the template
------------------------

A ``{% zone %}`` block marks the slice of a page template the server can re-render on its own.

.. code-block:: jinja
   :caption: catalog/template.djx

   {% zone "catalog-results" %}
     {% for product in page_obj.products %}
       <p data-next-key="{{ product.pk }}">{{ product.title }}</p>
     {% endfor %}
   {% endzone %}

The tag wraps the body in ``<div data-next-zone="catalog-results">``, which is the address the server patches later.
A zone is an optimisation rather than required markup, and :doc:`zones` covers what the default without one costs.

The request
-----------

An interaction issues a partial request instead of a full navigation.
A form submit, an auto-submitting filter, a paginating link, a lazy zone entering the viewport, and a Server-Sent Events message all reach the same pipeline.

.. code-block:: http
   :caption: request

   GET /catalog/?q=ban HTTP/1.1
   X-Next-Request: 1
   Accept: application/vnd.next.patches+json, text/html;q=0.9
   X-Next-Zone: catalog-results

``X-Next-Request`` is the switch the server reads to choose a partial response over the full page, and ``X-Next-Zone`` names the zones to render, comma-joined for a batch.
The rest of the namespace carries the field names of a validate-only pass, the merge intent of a paginating request, the asset version the client holds, the request id a stream echo is matched against, and the page that hosts a layer.

The envelope
------------

The server renders the named zone alone and serializes the result through the configured ``PARTIAL_BACKENDS`` backend.

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "morph", "target": {"zone": "catalog-results"},
        "html": "<div data-next-zone=\"catalog-results\">…</div>"}
     ],
     "assets": [],
     "form": null
   }

Every operation and every address is authored by the server, so the client is never asked to do anything the server did not name.
``version`` is the asset version the envelope was built under, which the client compares against the version it already holds and answers with one full reload when the two disagree, see :doc:`reference`.
:doc:`reference` lists the verbs, the addresses, the manifest fields, and the headers in tables.

The view branches into that zone response before the page renders, so the layout chain is never rendered and no markup is produced only to be dropped.
The addressed bodies still render against the page context, and a ``@context`` callable bound to another zone of the same page does not run for the batch.
Data that is expensive to produce guards itself with ``zone_requested``, which keeps it off the full render too, and :doc:`scenarios` shows that guard in full.
See :doc:`/content/internals/request-lifecycle` for where the view branches.

The apply
---------

The client resolves ``catalog-results`` inside the page that GET fetched and morphs the live wrapper against the new markup.
The morph reconciles the subtree in place, so focus, the caret, and a field the user is editing survive the update, and a row matched by ``data-next-key`` keeps its own state.
After the operations apply, ``next:mounted`` fires on every touched node, and before a node detaches ``next:removed`` fires on it.

Without the runtime the same URL answers with the whole document, so the filter stays a plain GET form and the page reloads.

See also
--------

.. seealso::

   :doc:`scenarios` for the same flow shown as seven concrete tasks.
   :doc:`reference` for the verbs, headers, and attributes in tables.
   :doc:`/content/ref/client` for the ``window.Next`` runtime that applies the envelope.
