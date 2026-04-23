Project layout
==============

.. _project-layout:

next.dj projects share a small set of conventional filenames. The names below
are not enforced by the framework, but every example in this repository uses
them. Following the same conventions in your own projects keeps code readable
for anyone familiar with next.dj.

A complete reference scaffold lives under ``examples/_template/``. Copy it to
start a new app.

File naming
-----------

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - What
     - File
   * - Custom backends (Router / Components / Forms / Static / TemplateLoader)
     - ``backends.py``
   * - Custom dependency-injection providers
     - ``providers.py``
   * - Django context processors
     - ``context_processors.py``
   * - Signal receivers
     - ``receivers.py``
   * - Custom template loaders (when split out)
     - ``loaders.py``
   * - Middleware
     - ``middleware.py``
   * - ``StaticCollector`` strategies (dedup or JS-context policy)
     - ``static_policies.py``
   * - Serializers for ``@context(serialize=True)`` payloads
     - ``serializers.py``
   * - Cached queries and cache helpers
     - ``queries.py``, ``cache.py``
   * - Django management commands
     - ``management/commands/``

These filenames read well from both the terminal and diffs. They also make the
role of a file obvious without opening it.

Pages and components directories
--------------------------------

``PAGES_DIR`` and ``COMPONENTS_DIR`` are user-controlled. Every example in
this repository overrides both, which shows that the defaults are only
defaults. Pick names that fit your domain.

.. code-block:: python

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "DIRS": [],
               "PAGES_DIR": "routes",
               "OPTIONS": {},
           },
       ],
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [],
               "COMPONENTS_DIR": "_widgets",
           },
       ],
   }

The table below tracks the names used by examples in this repository. Each
one is deliberately different so the convention is visible.

.. list-table::
   :header-rows: 1

   * - Example
     - Pages dir
     - Components dir
   * - ``shortener``
     - ``routes``
     - ``_widgets``
   * - ``markdown-blog``
     - ``screens``
     - ``_parts``
   * - ``audit-forms``
     - ``views``
     - ``_blocks``
   * - ``feature-flags``
     - ``panels``
     - ``_chunks``
   * - ``live-polls``
     - ``screens``
     - ``_widgets``
   * - ``kanban``
     - ``boards``
     - ``_pieces``
   * - ``multi-tenant``
     - ``workspaces``
     - ``_blocks``
   * - ``search-catalog``
     - ``storefront``
     - ``_cards``
   * - ``wiki``
     - ``routes``
     - ``_blocks``
   * - ``observability``
     - ``dashboards``
     - ``_widgets``

Shared root pages
-----------------

Two settings keys accept explicit ``DIRS`` entries so multiple Django apps
can share a common page tree and component scope.

.. code-block:: python

   "DEFAULT_PAGE_BACKENDS": [
       {
           "BACKEND": "next.urls.FileRouterBackend",
           "APP_DIRS": True,
           "PAGES_DIR": "routes",
           "DIRS": [BASE_DIR / "root_pages"],
           "OPTIONS": {},
       },
   ],
   "DEFAULT_COMPONENT_BACKENDS": [
       {
           "BACKEND": "next.components.FileComponentsBackend",
           "COMPONENTS_DIR": "_blocks",
           "DIRS": [BASE_DIR / "root_blocks"],
       },
   ],

``root_pages`` lets a shared root layout live next to each app's own
``routes``. ``root_blocks`` lets you publish components available to every
page.

Layout directives
-----------------

Nested layouts are only valuable when multiple child routes share chrome such
as a toolbar or sidebar. A section with a single page should skip its nested
``layout.djx``.

Inherit context through ``@context(inherit_context=True)`` when an ancestor
layout genuinely produces a value that every descendant uses, for example a
tenant badge or a board identifier. Do not thread every piece of data through
inherit hooks.

Static assets
-------------

Keep CSS and JS next to the ``page.py`` or ``component.py`` that owns them.
The framework discovers the co-located files automatically and injects them
through the ``{% collect_styles %}`` and ``{% collect_scripts %}`` slots.

For shared CDN assets such as Tailwind Play or React, call
``{% use_script %}`` in the root layout. The dedup strategy ensures the same
URL is emitted only once no matter how many components reference it.

Further reading
---------------

``docs/content/guide/testing`` — how the test helpers use this layout.

``docs/content/guide/components`` — component scoping rules and the
``_widgets`` directory convention.

``docs/content/guide/file-router`` — bracket segments and how ``routes``
becomes the URL tree.
