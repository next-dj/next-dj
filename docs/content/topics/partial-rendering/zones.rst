.. _topics-partial-rendering-zones:

Zones Save Render and Traffic
=============================

A zone is an optimisation, not required markup.
Partial rendering works on pages with no zones at all.
This page explains what a zone buys, what the default costs without one, and the rule for dynamic list rows.

.. contents::
   :local:
   :depth: 1

The Default Without a Zone
--------------------------

The default shape of an invalid form submission is an extract-morph.
The server re-renders the whole origin page through the existing re-render path and sends it with ``extract: true``.
The client parses the document, trims out the failed form by its ``data-next-action`` uid, and morphs only that form into the live page.

The result on screen is correct.
Only the failed form changes, the neighbouring forms keep their typed input, the caret stays put.
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

This is the price today, not a regression, because an invalid submission always re-rendered the full page before partial rendering.
The runtime turns that same full render into a targeted DOM update for free.

Adding a Zone
-------------

Wrapping the form in a ``{% zone %}`` and naming it on the tag trades the full render for a single-zone render.

.. code-block:: jinja
   :caption: a zoned form

   {% zone "rename-board" %}
     {% form "rename_board_form" zone="rename-board" %}…{% endform %}
   {% endzone %}

The ``zone="rename-board"`` argument compiles to ``data-next-target`` on the ``<form>``.
The submission carries the zone name, and the server re-renders only that zone with the bound form bound into its context.
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

The network payload shrinks from a page to a zone, and the server render shrinks with it.
Reach for a zone when a page is heavy, when a form sits among expensive siblings, or when the response size matters.
Leave it off when the page is small and the extract default already does the job.

The Wrapper Element
-------------------

A zone wraps its body in ``<div data-next-zone="name">`` by default.
A ``<div>`` is not valid everywhere.
Inside a ``<ul>``, a ``<select>``, or a ``<table>`` the parser would drop it, so name the wrapping element with ``tag=``.

.. code-block:: jinja
   :caption: a list zone and a table zone

   {% zone "catalog-results" tag="ul" %}
     {% for product in page_obj.products %}
       <li data-next-key="{{ product.pk }}">{% component "product_card" %}</li>
     {% endfor %}
   {% endzone %}

   <table>
     <thead>…</thead>
     {% zone "audit-rows" tag="tbody" %}
       {% for entry in entries %}
         {% component "audit_row" %}
       {% endfor %}
     {% endzone %}
   </table>

The wrapper carries ``data-next-zone`` regardless of the tag, so the zone stays addressable.

Key Your Dynamic List Rows
--------------------------

The morph engine matches old nodes to new ones to preserve identity, focus, and the caret.
A node it reuses keeps its own state, scroll position included, because the node never leaves the document.
A node it replaces wholesale loses that state, so matching is what keeps a row stable.
For a list of rows the engine needs a stable key.
Give every row of a dynamic list a ``data-next-key`` or an ``id``.
The engine reads identity from ``data-next-key`` first and falls back to ``id`` when no key is present.
A row that carries both earns a console warning, and the key wins.

.. code-block:: jinja
   :caption: keyed rows

   {% for product in page_obj.products %}
     <li data-next-key="{{ product.pk }}">{% component "product_card" %}</li>
   {% endfor %}

A keyless morph matches rows by position.
When a row is inserted or removed at the top of the list, every row below it shifts by one, and the morph rewrites labels and re-runs widgets that should have stayed put.
A key pins each row to its data, so an insert moves one node and leaves the rest untouched.
This is a documented limitation of a keyless morph, and the fix is one attribute per row.

``data-next-key`` also drives the dedup of ``append`` and ``prepend``.
A merge that brings a row whose key already exists replaces the existing row rather than duplicating it, which is what keeps infinite scroll free of duplicate rows under a race.

The morph leaves an ``<input type="file">`` untouched, so a file the user already chose survives a morph of the surrounding zone.
A multipart selection is never reset by a re-render of the form around it.

A ``<details>`` the user toggled keeps its open state across a morph, because a ``toggle`` event stamps the element dirty and the morph then skips its ``open`` attribute.
A ``<details>`` the user never touched takes whatever ``open`` state the server sends.
An element the server renders open on the first paint and closed on a later morph collapses, since no toggle ever stamped it.
Render the same ``open`` state the client should keep, or let a real toggle carry the state forward.

A repeated form needs the same key.
A ``{% form %}`` rendered inside a ``{% for %}`` produces one instance per iteration, all sharing the action uid the morph addresses.
Give each instance a ``key=`` with a stable per-row value, ``{% form "rename_item" key=item.pk %}``, so an invalid submit re-renders the submitted instance rather than the first one on the page.
A wrapping ``zone=`` is the alternative, and a looped form with neither raises ``next.W070`` at ``manage.py check``.

``next.W070`` catches a ``{% form %}`` written directly inside a ``{% for %}`` of a composed page.
It does not descend into a component template, so a form inside a ``{% component %}`` that a loop renders is not flagged.
The remedy is the same either way.
Thread a ``key=`` with a stable per-row value into the form, and the repeated morph lands on the submitted instance even when the form lives inside a looped component.

Zone Rules the Checks Enforce
-----------------------------

A few placements break a standalone zone render, and a system check catches each one before a request reaches it.

A zone name must be unique within a page's composed template, the layout chain plus the page template.
A zone may not sit inside a ``{% for %}`` or a ``{% if %}``, because a standalone render does not see loop variables or the condition that gated the block.
A ``lazy=`` zone needs a ``{% placeholder %}`` branch.
A zone belongs to a page, not a component, so a ``{% zone %}`` in a component template is rejected.
See :doc:`/content/ref/system-checks` for the full list and the check codes.

See Also
--------

.. seealso::

   :doc:`scenarios` for zones in the context of a full task.
   :doc:`reference` for the patch verbs and the zone tag attributes.
   :doc:`/content/ref/system-checks` for the zone placement checks.
