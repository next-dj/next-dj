.. _topics-partial-rendering:

Partial Rendering
=================

Partial rendering updates a slice of a page instead of reloading the whole document.
A form re-renders only the form that failed, a filter swaps only the result list, a modal carries a wizard, and a stream pushes fresh results to every open tab.
The server authors every DOM operation and the client applies it.
Selectors and swap strategies never cross the wire.

Every interaction in this section degrades to a full page cycle when JavaScript is off.
The runtime is an enhancement layered on top of the same ``POST`` then ``303`` then ``GET`` flow the framework already serves.
A page that works without the runtime keeps working with it, and gains the partial behaviour for free.

Read :doc:`scenarios` first.
It walks six concrete tasks from markup to handler, and the rest of the section deepens one concern at a time.

.. rubric:: The tutorial

:doc:`scenarios`
   Six scenarios from task to markup to handler: neighbouring forms, inline validation,
   an auto-submitting filter, pagination and infinite scroll, a live stream, and a modal
   wizard that refreshes a list.

.. rubric:: Concepts

:doc:`how-it-works`
   One partial update followed end to end, from the zone in the template to the envelope on the wire to the morph in the browser.

:doc:`zones`
   Why zones are an optimisation rather than required markup, what the extract default costs today, and the keying rule for dynamic list rows.

:doc:`done-choreographies`
   The two ways a wizard inside a modal refreshes the list on the page beneath it, compared honestly.

:doc:`co-located-js`
   Three idioms for co-located JavaScript that survives a partial update, and the one anti-pattern that does not.

:doc:`framework-islands`
   Mounting a Vue or React root into a zone and unmounting it cleanly, through the events and the preservation attribute the runtime ships.

:doc:`sse`
   Streaming patch envelopes over Server-Sent Events, the WSGI and ASGI contract, and the refresh fan-out pattern.

:doc:`extending`
   The three seams that open the protocol to an application: a custom verb, a server-pushed context value, and a server-fired event.

.. rubric:: Reference

:doc:`reference`
   The patch verbs, request and response headers, ``data-next-*`` attributes, and ``PARTIAL_BACKENDS`` settings, in tables.

.. seealso::

   :doc:`/content/security/csp-and-nonce` for serving the runtime under a Content Security Policy.

.. toctree::
   :hidden:
   :maxdepth: 1

   scenarios
   how-it-works
   zones
   done-choreographies
   co-located-js
   framework-islands
   sse
   extending
   reference
