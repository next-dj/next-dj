.. _topics-partial-rendering-zones:

Zones save render and traffic
=============================

A zone is an optimisation, not required markup.
Partial rendering works on pages with no zones at all.
This page explains what a zone buys, what the default costs without one, and the rule for dynamic list rows.
The wire always carries the ``assets`` and ``form`` keys, serialised as ``[]`` and ``null`` when empty.
The JSON examples on this page omit them when they are empty.

.. contents::
   :local:
   :depth: 1

The default without a zone
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

The extract default costs no more than the no-runtime cycle, which re-renders the full page on every invalid submission.
The runtime turns that same full render into a targeted DOM update for free.

Adding a zone
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

Zone assets on a standalone render
----------------------------------

A standalone zone render collects the co-located assets its body registers, component widgets included.
The envelope carries them outward as an asset manifest, URL-form and inline alike.
The client loads only what the page does not already have, inserting the link-verb assets before the operations apply and the script and module verbs after.
Each asset executes once per page lifetime.
The verb comes from the renderer registered for the asset kind, so a kind registered with a custom renderer is skipped and reaches the browser only on a full render.
An inline body keeps that verb only when the kind wraps it in the element the runtime builds, so a body of a kind that wraps it differently, or not at all, also stays with the full render.
On a zone ``GET`` the envelope also ships the values of the page's ``serialize=True`` context providers, introduced in :doc:`/content/topics/context`, as a ``context`` patch.
``Next.context`` therefore stays in step with the re-rendered zone.
See :doc:`co-located-js` for what once-per-page execution means for behaviour.
See :doc:`/content/topics/static-assets/asset-kinds` for the renderer that decides the verb, and :doc:`/content/topics/static-assets/index` for how the assets are discovered and bundled.

Poll a zone on an interval
--------------------------

A zone polls itself when the tag carries a ``poll=`` interval.
The body renders inline on the first paint, and the runtime re-GETs the zone on the interval through the same zone request a lazy load uses.

.. code-block:: jinja
   :caption: a polling zone

   {% zone "overview-totals" poll="5s" %}
     {% component "stat_card" label="Pages" value=totals.pages %}
   {% endzone %}

The interval reads ``5s``, ``1500ms``, or a bare number of milliseconds.
An interval below one second or above the browser timer ceiling fails when the template compiles, the same honest-fail as a malformed literal or an unknown ``lazy=`` trigger.

The wrapper carries ``data-next-poll`` with the resolved milliseconds on the full render.
A partial response for the zone keeps the interval on its wrapper and drops only the lazy hint, so the live element stays the source of truth across morphs.
Zones that share an interval batch into one zone request per owning page.
A hidden tab holds no poll timers.
On its return to the foreground each zone refetches only when its own interval elapsed while hidden, and otherwise the countdown resumes with the remaining time.
A brief flicker between tabs therefore fetches nothing, and switching windows does not storm the server.
A polling zone shows its body, so it cannot also be ``lazy=``, the two modes are exclusive and combining them is a compile error.

The wrapper element
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

A ``<details>`` the user toggled keeps its open state across a morph, because a ``toggle`` event marks the element touched and the morph then skips its ``open`` attribute for the life of the page.
The ``open`` state has no live focus signal a form field relies on, so once the user has toggled it the state is theirs even against a poll or stream patch they never asked for.
A ``<details>`` the user never touched takes whatever ``open`` state the server sends.
An element the server renders open on the first paint and closed on a later morph collapses, since no toggle ever stamped it.
Render the same ``open`` state the client should keep, or let a real toggle carry the state forward.

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

A zone name must be unique within a page's composed template, the layout chain plus the page template.
A zone may not sit inside a ``{% for %}`` or a ``{% if %}``, because a standalone render does not see loop variables or the condition that gated the block.
A ``lazy=`` zone needs a ``{% placeholder %}`` branch.
A zone belongs to a page, not a component, so a ``{% zone %}`` in a component template is rejected.
See :doc:`/content/ref/system-checks` for the full list and the check codes.

See also
--------

.. seealso::

   :doc:`scenarios` for zones in the context of a full task.
   :doc:`reference` for the patch verbs and the zone tag attributes.
   :doc:`/content/ref/system-checks` for the zone placement checks.
