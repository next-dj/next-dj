.. _topics-partial-rendering-zones:

Zones
=====

A page region that actions and polls address is a zone and is always inline.
Anything deferred, streamed or loaded later is a component with ``loading.djx``, or a page with ``loading.djx``.

A zone is an optimisation, not required markup.
Partial rendering works on pages with no zones at all.
This page explains what a zone buys and what the default costs without one.
It then covers zone assets, polling, what a zone shows while it reloads, the wrapper element, the keying rule for dynamic list rows, and the placements the checks reject.
The JSON examples on this page omit the empty ``assets`` and ``form`` keys, see :doc:`reference`.

.. contents::
   :local:
   :depth: 1

The default without a zone
--------------------------

The default shape of an invalid form submission is an extract-morph.
The server re-renders the whole origin page through the existing re-render path and sends it with ``extract: true``.
The client parses the document, trims out the failed form by its ``data-next-action`` uid, and morphs only that form into the live page.

The result on screen is correct.
Only the failed form changes.
The neighbouring forms keep their typed input, and the caret stays put.
The cost is on the server, not the DOM.
The whole page renders even though one form is kept.

.. code-block:: json
   :caption: an extract-morph envelope

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "morph", "target": {"form": "ab12cd34"}, "extract": true,
        "html": "<!doctype html>…the whole page…"}
     ],
     "form": {"uid": "ab12cd34", "valid": false, "errors": {"title": ["…"]}}
   }

The extract default costs no more on the server than the no-runtime cycle, which re-renders the full page on every invalid submission.
What the runtime adds is the targeted DOM update, at no extra render cost.

Adding a zone
-------------

Wrapping the form in a ``{% zone %}`` and naming it on the tag trades the full render for a single-zone render.

.. code-block:: jinja
   :caption: a zoned form

   {% zone "rename-board" %}
     {% form "rename_board_form" zone="rename-board" %}…{% endform %}
   {% endzone %}

The ``zone="rename-board"`` argument compiles to ``data-next-target`` on the ``<form>``.
The submission carries the zone name, and the server re-renders only that zone with the bound form in its context.
The envelope addresses the zone by name and the client morphs it in place.

.. code-block:: json
   :caption: a zone morph envelope

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "morph", "target": {"zone": "rename-board"},
        "html": "<div data-next-zone=\"rename-board\">…the one form…</div>"}
     ],
     "form": {"uid": "ab12cd34", "valid": false, "errors": {"title": ["…"]}}
   }

A page with ``serialize=True`` providers adds a ``context`` op after the morph, see `The context patch`_.

The network payload shrinks from a page to a zone, and the server render shrinks with it.
Add a zone when the page is heavy or the response size matters.

Zone assets on a standalone render
----------------------------------

A standalone zone render collects the co-located assets its body registers, component widgets included.
The envelope carries them outward as an asset manifest, URL-form and inline alike.
The client loads only what the page does not already have.
It inserts the link-verb assets before the operations apply and the script and module verbs after.
Each asset executes once per page lifetime, see :doc:`co-located-js` for what that means for behaviour.

The verb comes from the renderer registered for the asset kind.
A kind registered with a custom renderer is skipped and reaches the browser only on a full render.
An inline body keeps that verb only when the kind wraps it in the element the runtime builds.
A body of a kind that wraps it differently, or not at all, also stays with the full render.
See :doc:`/content/topics/static-assets/asset-kinds` for the renderer that decides the verb, and :doc:`/content/topics/static-assets/index` for how the assets are discovered and bundled.

The context patch
~~~~~~~~~~~~~~~~~

The envelope of a zone ``GET`` also ships the values of the page's ``serialize=True`` context providers as a ``context`` patch, see :doc:`/content/topics/context`.
A ``morph(zone=)`` from a handler ships it the same way.
``Next.context`` therefore stays in step with the re-rendered zone.
``context=False`` keeps the morph alone, and a mapping merges its values over the zone's own.
The :doc:`extending` page covers both.
A zone declared inside the body of another renders as part of that body, so a standalone render of the outer zone also runs the context functions bound to the inner one.

Poll a zone on an interval
--------------------------

A zone polls itself when the tag carries a ``poll=`` interval.
The body renders inline on the first paint, and the runtime re-GETs the zone on the interval through the same zone request an action or a filter uses.

.. code-block:: jinja
   :caption: a polling zone

   {% zone "overview-totals" poll="5s" %}
     {% component "stat_card" label="Pages" value=totals.pages %}
   {% endzone %}

The interval reads ``5s``, ``1500ms``, or a bare number of milliseconds.
An interval below one second or above the browser timer ceiling fails when the template compiles, the same failure as a malformed literal or an unknown option.

The wrapper carries ``data-next-poll`` with the resolved milliseconds on the full render.
A partial response for the zone keeps the interval on its wrapper, so the live element stays the source of truth across morphs.
Zones that share an interval batch into one zone request per owning page.
A hidden tab holds no poll timers.
On its return to the foreground each zone refetches only when its own interval elapsed while hidden, and otherwise the countdown resumes with the remaining time.
A brief flicker between tabs therefore fetches nothing, and switching windows does not storm the server.
A context function tagged ``@context(..., zone="name")`` does not run on a foreign poll tick.
A poll GET runs the zone-less callables plus the ones bound to the polled zone, see :doc:`/content/topics/context`.

The interval is a client timer, so a page without the runtime shows the body of the first paint and never refreshes it.
A polling zone is therefore an enhancement over what the page already renders rather than the only path to the value, and a page that has to stay fresh without JavaScript keeps a plain reload control beside the zone.

What a zone shows while it reloads
----------------------------------

A zone carries ``data-next-busy`` and ``aria-busy`` while a request reloads it, whether an action, a filter, a poll, or a stream asked for it.
``reload=`` decides what the zone shows in the meantime.

``reload="keep"``, the default, keeps the current content on screen, marked busy, until the new content arrives.
A component boundary inside the zone that held ready content keeps it as well until its own content lands, so a fast reload never flashes a skeleton.

``reload="loading"`` shows loading content once the request outlasts ``delay=``, 150 milliseconds unless the tag names another duration such as ``"300ms"`` or ``"1s"``.
``delay=`` belongs to ``reload="loading"`` alone, and a zone that keeps its content refuses it when the template compiles.
Every component boundary inside the zone shows its own ``loading.djx``.
The zone itself shows its reload skeleton when it has one, a file named ``loading.<zone>.djx`` beside the template that declares the zone.

.. code-block:: jinja
   :caption: pages/catalog/template.djx

   {% zone "catalog-results" tag="tbody" reload="loading" delay="300ms" %}
     {% for product in page_obj %}
       <tr data-next-key="{{ product.pk }}">…</tr>
     {% endfor %}
   {% endzone %}

.. code-block:: jinja
   :caption: pages/catalog/loading.catalog-results.djx

   {% for _ in "xxxxx" %}<tr><td colspan="4" class="skeleton"></td></tr>{% endfor %}

The skeleton belongs to the template beside it, a ``template.djx``, a ``layout.djx`` or a ``component.djx``, and is never inherited by a directory below.
It never renders on the first paint, because a zone always renders inline, and only ``reload="loading"`` uses it.
A page whose zone uses ``reload="loading"`` ships the skeleton once per response, rendered with the zone's context inside ``<template data-next-loading="<zone>">``, and ships the ``loading.djx`` of every ``defer="inline"`` component inside the zone the same way, named by its boundary key.
Without the file, a zone with no component boundary inside stays busy only.
The loading content never replaces a part of the zone that holds the focused element or a field the user changed.
A replaced part keeps its measured height as ``min-height`` while the loading content shows, so the page below it does not jump, and ``data-next-reserve="off"`` on the zone or on an element around it lets the part shrink to the loading content instead.
``next.E232`` reports a ``loading.<zone>.djx`` whose template declares no zone of that name.

A zone is not an error boundary, so there is no ``error.<zone>.djx``.
A failure inside the zone falls to the nearest page or component ``error.djx``.

The wrapper element
-------------------

A zone wraps its body in ``<div data-next-zone="name">`` by default.
A ``<div>`` is not valid everywhere.
Inside a ``<ul>``, a ``<select>``, or a ``<table>`` the parser would drop it, so name the wrapping element with ``tag=``.

.. code-block:: jinja
   :caption: a list zone

   {% zone "catalog-results" tag="ul" %}
     {% for product in page_obj %}
       <li data-next-key="{{ product.pk }}">{% component "product_card" %}</li>
     {% endfor %}
   {% endzone %}

Here ``page_obj`` is a Django :class:`~django.core.paginator.Page`, published by the paginated context function of :ref:`topics-pages-pagination`.
A ``Page`` iterates over its own rows, so the loop reads it directly.
A table zone names ``tag="tbody"`` for the same reason, shown in the pagination scenario of :doc:`scenarios`.
The wrapper carries ``data-next-zone`` regardless of the tag, so the zone stays addressable.

The wrapper also takes the HTML global attributes, ``aria-*``, and ``data-*`` as quoted literals, so it can be the sticky or flex element of a layout itself.

.. code-block:: jinja
   :caption: a sticky header zone

   {% zone "site-header" tag="header" class="sticky top-0 flex" aria-label="Site" %}
     {% include "header.djx" %}
   {% endzone %}

A zone render writes the same attributes, so a refresh keeps them.
The hand-written router and toast attributes of :doc:`/content/ref/client` pass too, so a zone can be a ``data-next-nav`` container whose links the router marks, or the ``data-next-toasts`` tray the ``toast`` op fills, and so does ``data-next-reserve="off"``, the only value it takes.
Every other ``data-next-*`` name stays reserved for the runtime, since the runtime writes it on the wrapper or reads it to decide how the zone reloads.

Key your dynamic list rows
--------------------------

The morph engine matches old nodes to new ones to preserve identity, focus, and the caret.
A node it reuses keeps its own state, scroll position included, because the node never leaves the document.
A node it replaces wholesale loses that state, so matching is what keeps a row stable.
For a list of rows the engine needs a stable key.
Give every row of a dynamic list a ``data-next-key`` or an ``id``.
The engine reads identity from ``data-next-key`` first and falls back to ``id`` when no key is present.
A row that carries both earns a console warning in dev, and the key wins.

.. code-block:: jinja
   :caption: keyed rows

   {% for product in page_obj %}
     <li data-next-key="{{ product.pk }}">{% component "product_card" %}</li>
   {% endfor %}

A keyless morph matches rows by position.
When a row is inserted or removed at the top of the list, every row below it shifts by one, and the morph rewrites labels and re-runs widgets that should have stayed put.
A key pins each row to its data, so an insert moves one node and leaves the rest untouched.
This is a documented limitation of a keyless morph, and the fix is one attribute per row.

``data-next-key`` also drives the dedup of ``append`` and ``prepend``.
A merge that brings a row whose key already exists replaces the existing row rather than duplicating it, which is what keeps infinite scroll free of duplicate rows under a race.

Preserved state
~~~~~~~~~~~~~~~

The morph never rewrites the live selection of an ``<input type="file">``, so a file the user already chose survives a morph of the surrounding zone.
A multipart selection is never reset by a re-render of the form around it.

A ``<details>`` the user toggled keeps its open state across a morph, because a ``toggle`` event marks the element touched and the morph then skips its ``open`` attribute for the life of the page.
The ``open`` state has no live focus signal a form field relies on, so once the user has toggled it the state is theirs even against a poll or stream patch they never asked for.
A ``<details>`` the user never touched takes whatever ``open`` state the server sends.
An element the server renders open on the first paint and closed on a later morph collapses, since no toggle ever stamped it.
Render the same ``open`` state the client should keep, or let a real toggle carry the state forward.

Repeated forms
~~~~~~~~~~~~~~

A repeated form needs the same key.
A ``{% form %}`` rendered inside a ``{% for %}`` produces one instance per iteration, all sharing the action uid the morph addresses.
Give each instance a ``key=`` with a stable per-row value, ``{% form "rename_item" key=item.pk %}``, so an invalid submit re-renders the submitted instance rather than the first one on the page.
A wrapping ``zone=`` is the alternative, and a looped form with neither earns the ``next.W070`` warning at ``manage.py check``.

``next.W070`` catches a ``{% form %}`` written directly inside a ``{% for %}`` of a composed page.
It does not descend into a component template, so a form inside a ``{% component %}`` that a loop renders is not flagged.
The remedy is the same either way.
Thread a ``key=`` with a stable per-row value into the form, and the repeated morph lands on the submitted instance even when the form lives inside a looped component.

Zone rules the checks enforce
-----------------------------

A few placements break a standalone zone render, and a system check catches each one before a request reaches it.
A standalone render does not see loop variables, the condition that gated a block, or a permission decision.
At runtime every zone the ``next.E062``, ``next.E063``, and ``next.E161`` rules report counts as undeclared for every user.
A standalone request answers 400 ``unknown zone`` and ``Patches.morph_zone`` raises ``UnknownZoneError``.

.. list-table::
   :header-rows: 1
   :widths: 40 15 45

   * - Rule
     - Code
     - Effect
   * - A zone name is unique within the composed template, the layout chain plus the page template.
     - ``next.E060``
     - A duplicate name is reported.
   * - A zone does not sit inside a ``{% for %}`` or an ``{% if %}``, at any depth.
     - ``next.E062``, ``next.E063``
     - The standalone render sees neither the loop variable nor the condition.
   * - A zone does not sit under a block tag from a module outside ``django.`` and ``next.``.
     - ``next.E063``
     - Such a tag may hide its body in code the analysis cannot read, and the report names the tag.
   * - A zone does not sit inside a ``{% has_perm %}`` block of the page, the ``else`` branch included.
     - ``next.E161``
     - A standalone render does not ask the permission again.
   * - A zone in a component body, any slot or the children, is not rendered on its own when the component decides whether that part shows.
     - ``next.E161``
     - The permission decision lives inside the zone, see :ref:`topics-permissions-zones`.
   * - A zone does not sit directly inside a ``{% with %}``.
     - ``next.W067``
     - A warning, since the bindings are invisible to a standalone render.
       Move them into a context provider or inside the zone body.
   * - A zone belongs to a page, not a component.
     - ``next.E065``
     - A ``{% zone %}`` in a component template is rejected, and a component that loads later or shows a loading UI does so through its own ``loading.djx``.
   * - A ``loading.<zone>.djx`` sits beside the template that declares the zone.
     - ``next.E232``
     - A file whose zone the template beside it does not declare never renders.
   * - A zone name is an ASCII slug of letters, digits, hyphens, or underscores.
     - ``next.E061``
     - The name travels in the ``X-Next-Zone`` header, which is latin-1.

An unknown option or an invalid value of ``tag=``, ``poll=``, ``reload=``, ``delay=`` or ``data-next-reserve`` never reaches a check, since it raises ``TemplateSyntaxError`` when the template compiles.
See :doc:`/content/ref/system-checks` for the full list and the check codes.

See also
--------

.. seealso::

   :doc:`scenarios` for zones in the context of a full task.
   :doc:`reference` for the patch verbs and the zone tag attributes.
   :doc:`/content/ref/system-checks` for the zone placement checks.
