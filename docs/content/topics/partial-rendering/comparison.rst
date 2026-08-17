.. _topics-partial-rendering-comparison:

Zones compared with htmx, Turbo, and Unicorn
============================================

Zones answer a question several mature projects already answer.
htmx, Turbo, and Django Unicorn each ship a working partial-update model, each has a larger user base than next.dj, and for many projects one of them is the better choice.
This page sets the models side by side on four axes that can be checked rather than argued, and then names where each alternative fits better.
Claims about next.dj come from ``next/partial/`` and ``next/client/``.
Claims about the alternatives stay inside the model each project describes as its own, with no version-specific detail.

.. contents::
   :local:
   :depth: 1

The axes at a glance
--------------------

.. list-table::
   :header-rows: 1
   :widths: 14 22 22 20 22

   * - Approach
     - Who authorizes the patch
     - Where the selector lives
     - With JavaScript off
     - Who owns the wire format
   * - next.dj zones
     - The page view. A zone request resolves the page body before any zone renders.
     - On the server. The wire carries zone names, and every address in an envelope is written by a handler.
     - The same URL answers the whole document, and the interaction stays an ordinary link or form post.
     - The project. One protocol backend from ``PARTIAL_BACKENDS`` serialises every envelope.
   * - htmx
     - The view the attribute points at, like any Django view.
     - In the markup, on the element that triggers the request.
     - It depends on the trigger. An attribute over a real link or form leaves the plain navigation in place.
     - htmx. The body is an HTML fragment and the ``HX-`` response headers steer the client.
   * - Turbo
     - The view behind the frame or the stream.
     - In the frame identifier on both sides, and in the target of a stream element.
     - Links and forms stay links and forms, and a frame renders its content in place.
     - Turbo. Frames and streams are custom elements with a fixed action vocabulary.
   * - Django Unicorn
     - The component class that handles the action, the way a view handles a request.
     - Neither side names one. The component is the unit that updates.
     - The component renders once and its interactions do not run.
     - Django Unicorn. The endpoint, the payload, and the state round trip belong to the library.
   * - Full page reload
     - The view, and the whole document is the answer.
     - Nowhere.
     - Unchanged. This is the baseline every other row falls back to.
     - HTML.

Who authorizes the patch
------------------------

Every model here can enforce access, because every model puts a server view behind the request.
The axis that separates them is how much of that enforcement comes for free.
In next.dj a zone request is the same URL as the page, and the unified page view resolves the page body before it hands the request to the zone branch.
A redirect, a login bounce, or a denial the page already performs therefore stands before any zone renders, without a second guard written for the partial path.

Rendering a zone of a different page is the one case that leaves that path, and it carries its own check.
``Patches.morph_foreign_zone`` re-runs the foreign page's body resolution and raises ``ForeignPageNotAuthorizedError`` when the requester may not render it, before the zone renders.
The ``X-Next-Origin`` header is the exception a reader should know about, since it is client-supplied and validated same-site without being authorized, so a handler that morphs zones of the origin page re-checks access itself.

htmx and Turbo place the same responsibility on the view the request reaches, which is the ordinary Django position and is neither better nor worse in itself.
The difference is that the region to update is chosen in the template rather than derived from the page the server has already authorized.
Unicorn moves the decision into a component class, so the class is where both the action and its permission checks live.

Where the selector lives
------------------------

This is the axis where the models genuinely disagree.
In next.dj a template declares an address with ``{% zone "name" %}``, the client sends zone names in ``X-Next-Zone``, and the server writes every target of every patch.
A request naming a zone the page does not declare is a 400 before any render, and a verb no registry holds fails with ``UnknownPatchOpError`` rather than reaching the browser.
The single raw CSS selector a patch may carry is written by the server as an escape hatch, so no selector travels from the browser to the server at any point.
The cost is that a new update surface is a template edit plus a handler, not an attribute.

htmx puts the target and the swap strategy on the element that fires the request, which is its central strength.
Reading the element tells the whole story of the interaction, and no server change is needed to point an update somewhere else.
The matching cost is that a selector in a template and the markup it addresses drift independently, and that a template author can point a swap at any region of the document.
htmx also offers the other direction through out-of-band swaps and response headers, so a server that wants to take the addressing back can.

Turbo splits the axis.
A frame is addressed by a matching identifier that has to exist on both the page and the response, so the address is shared markup rather than a selector.
A stream element carries its action and its target from the server, which is the position closest to zones in this table.

Unicorn removes the question by making the component the unit of update.
Neither side writes an address, and the whole component subtree is what the library reconciles.

With JavaScript off
-------------------

next.dj switches to a partial response only when the request carries ``X-Next-Request``, and the runtime stamps that header.
Without the runtime the same URL answers with the full document, byte for byte.
The runtime also intercepts only elements carrying a ``data-next-*`` attribute, so a plain link navigates and a plain form posts.
A filter form stays a GET form, a paginating link stays a link to the next page, and a form written with the ``{% form %}`` tag falls back to the ``POST`` then ``303`` then ``GET`` cycle.

htmx degrades according to how the trigger is written.
An attribute layered over a real link or a real form leaves a working navigation underneath it, while an attribute on an element that is neither has no fallback to degrade to.
Turbo layers on ordinary links and forms as its normal mode, so the no-script path is the plain HTML path.
Unicorn renders its component once on the server and then needs its runtime for every interaction after that.

Who owns the wire format
------------------------

next.dj treats the wire as a project decision.
``PARTIAL_BACKENDS`` holds one active protocol backend, and ``PartialProtocolBackend`` exposes ``serialize_envelope`` and ``sse_event`` over the same envelope object.
The default serialises compact JSON under ``application/vnd.next.patches+json``, and a replacement changes the format without touching shaping or the registries.
The client side of the same seam is ``Next.partial.parseHook``, which turns a foreign content type into an envelope before the apply pipeline runs.
The cost of owning the format is that a custom envelope becomes a compatibility surface the project maintains itself, against a client that has to learn to read it.

htmx sends HTML, which needs no format decision at all and stays readable in the network panel.
Its steering vocabulary is the ``HX-`` response headers, defined by htmx.
Turbo defines a stream element with a fixed set of actions, extensible on the client, carried under its own content type.
Unicorn defines the endpoint and the state payload, which is what lets it round-trip component state without the application describing a wire at all.

Where each alternative fits better
----------------------------------

htmx
~~~~

Reach for htmx when the interactions are few and local, when the project keeps its existing views and URLconf, or when the server is not Django at all.
Its model is smaller than a framework, its published body of patterns is far larger than this section, and it adds nothing to the request path on the server.
It is also the right tool when the people writing the interactions are the people writing the templates, since the whole behaviour is visible in the element.
next.dj does not compete with it on the same page, and :doc:`/content/howto/drive-form-actions-with-htmx` shows the two running together over the same form action.

Turbo
~~~~~

Reach for Turbo when accelerating whole-application navigation is the goal rather than patching regions.
next.dj intercepts only elements carrying a ``data-next-*`` attribute and ships no client router, so it never turns every link in the document into a partial navigation, which is exactly what Turbo Drive is for.
Turbo is also the established choice for a project that already follows Hotwire idioms or wraps its web views in a native shell.

Django Unicorn
~~~~~~~~~~~~~~

Reach for Unicorn when the natural unit is a stateful component whose state lives on the server between interactions.
A zone renders from the page context on every request, and next.dj keeps no per-component state between requests, so an interaction that needs remembered state puts it in the URL, the form, or the database.
The one server-held exception is the multi-step wizard, which stores step drafts in the Django cache keyed by session, and that is a wizard rather than a general component-state mechanism.
Unicorn is also the shorter path when a team wants reactive behaviour without writing an endpoint for each interaction.

When zones are the better fit
-----------------------------

The case for zones is narrow and worth stating plainly.
Zones fit a project that wants the server to remain the only author of what the browser does, so that no template attribute can address a region the server did not choose to expose.
They fit a project that wants the partial path to inherit the page's authorization instead of re-deriving it, since a zone request runs the page view first.
They fit a team that wants the no-script path to stay a structural fallback rather than a discipline, since the partial branch turns on only under a header the runtime sends.

Everything this section documents is optional, and a next.dj site that declares no zone at all serves ordinary full pages.
:doc:`limitations` states where the model stops, and reading it beside this page completes the comparison.

See also
--------

.. seealso::

   :doc:`limitations` for the boundaries the model draws on purpose.
   :doc:`extending` for the three seams that open the protocol without forking the runtime.
   :doc:`/content/howto/drive-form-actions-with-htmx` for running htmx over a next.dj form action.
