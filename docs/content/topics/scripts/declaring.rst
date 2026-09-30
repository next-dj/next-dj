.. _topics-scripts-declaring:

Declaring scripts
=================

A ``scripts.py`` at the top of a page tree declares every third-party script the tree runs, each a ``Script`` value with a name, a source, a loading strategy, and a consent category.
The server writes the scripts a page may run at once into its head, and the runtime loads the rest later, on idle, on the first interaction, on demand, or once the visitor grants their category.
This page covers the declaration, the strategies, the head slot, per-page scripts, and the nonce.

.. contents::
   :local:
   :depth: 2

Declaring scripts
-----------------

.. code-block:: python
   :caption: shop/pages/scripts.py

   from django.conf import settings

   from next.scripts import Script, Strategy

   scripts = (
       Script(
           "plausible",
           src="https://plausible.io/js/script.manual.js",
           category="analytics",
           strategy=Strategy.DEFER,
           attrs={"data-domain": settings.PLAUSIBLE_DOMAIN},
       ),
       Script(
           "chat",
           src="https://widget.example/chat.js",
           category="preferences",
           strategy=Strategy.INTERACTION,
       ),
   )

``scripts`` is any iterable of ``Script``.
The manual Plausible variant sends a page view only when a script asks it to, which a small adapter does on every navigation, see :doc:`/content/howto/write-a-vendor-adapter`.
The file loads with the tree, and while ``DEBUG`` is on a render reads it again once its mtime moves, so an edit needs no restart.
A file that fails to import, or a ``scripts`` that holds anything but ``Script`` values, costs the tree all of its scripts, and ``manage.py check`` names the cause.

A ``Script`` takes these fields.

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Field
     - Meaning
   * - ``name``
     - The key ``{% script %}``, the runtime, and the ``data-next-script`` attribute know the script by, unique in the tree.
   * - ``src``
     - An absolute ``https`` URL or a staticfiles name, which the static pipeline resolves to its public URL.
   * - ``init``
     - Trusted inline JavaScript that runs before ``src`` loads, such as a vendor's queue stub.
   * - ``strategy``
     - When the script loads, ``Strategy.ASYNC`` by default.
   * - ``category``
     - The consent category, ``necessary`` by default, one of ``CONSENT["CATEGORIES"]``.
   * - ``auto``
     - ``True`` to run on every page of the tree, ``False`` to run only where ``{% script %}`` names it.
   * - ``attrs``
     - Extra attributes of the ``src`` tag, ``integrity``, ``crossorigin``, ``referrerpolicy``, and any ``data-*`` name.

A ``data-*`` attribute is how a vendor loader reads its configuration, Plausible its ``data-domain`` and a project adapter whatever it declares.
The system checks report a script with neither ``src`` nor ``init``, a ``src`` that no finder answers, an ``init`` that would close its element, an attribute outside the list, and a category the settings do not list.

Strategies
----------

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Strategy
     - Loads
   * - ``BLOCKING``
     - In the head, parser-blocking.
   * - ``ASYNC``
     - In the head, with ``async``.
   * - ``DEFER``
     - In the head, with ``defer``.
   * - ``IDLE``
     - From the runtime, once the page idles after load, at the latest three seconds later.
   * - ``INTERACTION``
     - From the runtime, on the first ``pointerdown``, ``keydown``, ``touchstart``, or ``scroll``.
   * - ``MANUAL``
     - From the runtime, when page code calls ``Next.scripts.load(name)``.

The first three render as ``<script>`` tags in the head when the visitor may run the script's category, and every other script rides the ``$scripts`` manifest of the init payload.
The runtime inserts a manifest entry once its strategy fires and its category is granted, the ``init`` body before the ``src``, and a tag the server already rendered is never inserted a second time.
A ``BLOCKING`` or ``DEFER`` entry the runtime inserts keeps its place among the others inserted with it, so a vendor loader declared before its adapter still runs first.
The runtime-loaded strategies need the runtime on the page, so they never run while ``NEXT_JS_OPTIONS["policy"]`` is ``disabled``.
A gated ``BLOCKING`` script blocks nothing once the runtime loads it, since by then the page has rendered.

The head slot
-------------

``{% collect_head %}`` marks where the head scripts go, and without it they go right before ``</head>``.

.. code-block:: jinja
   :caption: shop/pages/layout.djx

   <head>
     <meta charset="utf-8">
     {% metadata %}
     {% collect_styles %}
     {% collect_head %}
   </head>

The scripts render in the order ``scripts.py`` declares them, each tag carrying ``data-next-script="<name>"``, so a script that defines what a later one reads comes first in the tuple.

Per-page scripts
----------------

A script declared with ``auto=False`` runs only on the pages whose render meets ``{% script "<name>" %}``, in a layout, a page template, or a component.

.. code-block:: jinja
   :caption: shop/pages/lp/[campaign]/template.djx

   {% script "chat" %}
   <h1>{{ campaign.headline }}</h1>

The tag renders nothing where it stands and marks the script for the head or the manifest of this render.
A name the tree does not declare logs one warning per page.

Passing values into init
------------------------

``init`` is written by the project and trusted as JavaScript.
A value from settings or the database reaches the script through a ``data-*`` attribute of ``attrs`` instead, which the framework escapes, and the script reads it from ``document.currentScript.dataset``.

The nonce
---------

Every tag the framework writes carries the CSP nonce of the request, the runtime and its init payload, the inline ``use_script`` and ``use_style`` blocks, the head scripts, and the manifest entries the runtime inserts.
``NEXT_FRAMEWORK["CSP_NONCE"]`` switches it on and off, and the nonce is the one django-csp or Django's own CSP middleware minted for the request.
A script that inserts a vendor loader itself copies its own nonce onto it, see :doc:`/content/howto/write-a-vendor-adapter`.
:doc:`/content/security/csp-and-nonce` covers the policy side.

See also
--------

.. seealso::

   :doc:`consent` for the categories and the gate.
   :doc:`/content/howto/write-a-vendor-adapter` for Plausible and GA4 built on ``Script``.
   :doc:`/content/ref/scripts` for ``Script`` and ``Strategy``.
   :doc:`/content/ref/client-extras` for ``Next.scripts``.
