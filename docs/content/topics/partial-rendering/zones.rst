.. _topics-partial-rendering-zones:

Zones
=====

A zone is an optimisation, not required markup.
Partial rendering works on pages with no zones at all.
This page explains what a zone buys and what the default costs without one.
It then covers zone assets, polling, the wrapper element, the keying rule for dynamic list rows, and the placements the checks reject.
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

A zone GET and a ``morph(zone=)`` from a handler can follow the morph with a ``context`` op.
It carries the page's ``serialize=True`` values, so the envelope above has a second op when the page publishes any.

The network payload shrinks from a page to a zone, and the server render shrinks with it.
Reach for a zone when a page is heavy, when a form sits among expensive siblings, or when the response size matters.
Leave it off when the page is small and the extract default already does the job.

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

.. note::

   The HTML a patch carries is inserted as written and is not sanitized.
   The client strips ``<script>`` elements and nothing else.
   Authorization lives on the page that renders the zone, so escape user content before it reaches a patch.

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
An interval below one second or above the browser timer ceiling fails when the template compiles, the same failure as a malformed literal or an unknown ``lazy=`` trigger.

The wrapper carries ``data-next-poll`` with the resolved milliseconds on the full render.
A partial response for the zone keeps the interval on its wrapper and drops only the lazy hint, so the live element stays the source of truth across morphs.
Zones that share an interval batch into one zone request per owning page.
A hidden tab holds no poll timers.
On its return to the foreground each zone refetches only when its own interval elapsed while hidden, and otherwise the countdown resumes with the remaining time.
A brief flicker between tabs therefore fetches nothing, and switching windows does not storm the server.
A polling zone shows its body, so it cannot also be ``lazy=``.
The two modes are exclusive and combining them is a compile error.
A context function tagged ``@context(..., zone="name")`` does not run on a foreign poll tick.
A poll GET runs the zone-less callables plus the ones bound to the polled zone, see :doc:`/content/topics/context`.

The interval is a client timer, so a page without the runtime shows the body of the first paint and never refreshes it.
A polling zone is therefore an enhancement over what the page already renders rather than the only path to the value, and a page that has to stay fresh without JavaScript keeps a plain reload control beside the zone.

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

Key your dynamic list rows
--------------------------

Keys
~~~~

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
At runtime every zone these rules report counts as undeclared for every user.
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
   * - A ``lazy=`` zone has a ``{% placeholder %}`` branch that holds something.
     - ``next.E064``
     - A branch of whitespace alone is reported, because the check reads the significant nodes.
   * - A zone belongs to a page, not a component.
     - ``next.E065``
     - A ``{% zone %}`` in a component template is rejected.
   * - A zone name is an ASCII slug of letters, digits, hyphens, or underscores.
     - ``next.E061``
     - The name travels in the ``X-Next-Zone`` header, which is latin-1.

A ``{% placeholder %}`` inside a zone that declares no ``lazy=`` never reaches a check.
It raises ``TemplateSyntaxError`` when the template compiles, since such a zone shows its body and never renders the branch.
See :doc:`/content/ref/system-checks` for the full list and the check codes.

See also
--------

.. seealso::

   :doc:`scenarios` for zones in the context of a full task.
   :doc:`reference` for the patch verbs and the zone tag attributes.
   :doc:`/content/ref/system-checks` for the zone placement checks.
