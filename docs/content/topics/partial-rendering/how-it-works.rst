.. _topics-partial-rendering-how-it-works:

How a Partial Request Flows
===========================

A partial update is one request and one response laid over the ordinary page cycle.
The server authors every DOM operation, the client applies it, and the wire carries verbs and addresses rather than selectors or swap strategies.
This page follows one update end to end.

.. contents::
   :local:
   :depth: 1

The Page and Its Zones
----------------------

A directory maps to a URL and a ``page.py`` turns a segment into a page, rendered through a ``.djx`` template.
A ``{% zone %}`` block marks a slice of that template the server can re-render on its own.
A zone is an optimisation rather than required markup.
The server can address a page region without one, and a zone names the region so the response carries only the slice instead of the whole document.

The Request
-----------

An interaction issues a partial request instead of a full navigation.
A form submit, an auto-submitting filter, a paginating link, a lazy zone scrolling into view, and a Server-Sent Events message each reach the same pipeline.
The request carries an ``Accept`` of the patch media type, which doubles as the switch
the server reads to choose a partial response over a full page, and ``X-Next-*`` headers
that name the zone, the origin page, and the asset version.

The Envelope
------------

The server shapes a patch envelope and serialises it through the configured ``PARTIAL_BACKENDS`` backend.
The envelope carries a version, an ordered list of operations, an asset manifest, optional form metadata, and an optional rotated CSRF token.
The server authors every operation and every address.
A selector or a swap strategy never crosses the wire, so the client cannot be asked to do anything the server did not name.

The Apply
---------

The client narrows the envelope and runs each operation against the addressed zone, resolving a layer zone before the same-named page zone.
The built-in verbs are ``morph``, ``replace``, ``inner``, ``append``, ``prepend``, ``remove``, ``refresh``, ``event``, ``toast``, ``url``, ``visit``, ``layer.open``, ``layer.close``, and ``context``.
``morph`` is the default, reconciling the live subtree in place against the new markup so focus, the caret, and a field the user is editing survive the update.
A reused node keeps its own state, scroll position included, because it never leaves the document.
``append`` and ``prepend`` dedupe by key so a re-fetched page of a list cannot double its rows.

The morph engine protects a focused input and a dirty field from the server value, leaves a ``data-next-keep`` node untouched, and treats a custom element or a shadow root as atomic.
After the operations apply, ``next:mounted`` fires on every touched node, and before any node detaches ``next:removed`` fires on it, the pair a framework island binds to.

The Surfaces Around the Apply
-----------------------------

A modal opens through the layer stack, which pushes the honest URL of the modal body so the modal is shareable and a refresh resolves the URL as its own standalone page, and Back closes the top layer.
A Server-Sent Events stream feeds the same apply pipeline, suppressing the echo of the client's own mutation and revalidating its zones on returning visibility.
A lazy zone and a trigger pull their own follow-up request through the same wire.

Degrading Without JavaScript
----------------------------

Every interaction degrades to a full page cycle when the runtime is absent.
The runtime is an enhancement over the ``POST`` then ``303`` then ``GET`` flow the framework already serves.
A page that works without it keeps working with it and gains the partial behaviour for free.

See Also
--------

.. seealso::

   :doc:`scenarios` for the same flow shown as seven concrete tasks.
   :doc:`reference` for the verbs, headers, attributes, and client runtime surface in tables.
