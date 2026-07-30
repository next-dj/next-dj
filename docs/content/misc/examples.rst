.. _misc-examples:

Repository examples
===================

The ``examples/`` tree in the next.dj repository holds self-contained Django projects.
Each runs on SQLite and in-process ``LocMemCache``.
No Docker, Node, or external services are required beyond what the :repo:`examples README <blob/main/examples/README.md>` lists for a given folder.

Catalog
-------

Every row links to the folder on GitHub and summarises the focus in one line.
The **Primary docs** column points to the sections of this manual where the techniques appear.

.. list-table::
   :header-rows: 1
   :widths: 18 42 40

   * - Folder
     - Focus
     - Primary docs
   * - :repo:`shortener <tree/main/examples/shortener>`
     - File router, DI providers, LocMemCache, ``{% zone %}`` lists patched through ``Patches`` envelopes, management command
     - :doc:`/content/topics/file-router`, :doc:`/content/topics/dependency-injection`, :doc:`/content/topics/partial-rendering/index`
   * - :repo:`markdown-blog <tree/main/examples/markdown-blog>`
     - Markdown posts, nested layouts, ``@context(serialize=True)``, context processor, co-located ``component.js``
     - :doc:`/content/topics/layouts`, :doc:`/content/topics/context`, :doc:`/content/topics/static-assets/js-context`
   * - :repo:`feature-flags <tree/main/examples/feature-flags>`
     - Composite ``feature_guard``, signal receivers, cache invalidation
     - :doc:`/content/topics/components`, :doc:`/content/topics/signals`
   * - :repo:`audit-forms <tree/main/examples/audit-forms>`
     - Custom ``FormActionBackend``, ``action_dispatched`` / ``form_validation_failed`` signals, dual audit channels, ``FormWizard`` in a modal layer, lazy audit-table zone
     - :doc:`/content/topics/forms/wizard`, :doc:`/content/topics/forms/backends`, :doc:`/content/topics/forms/signals`, :doc:`/content/topics/partial-rendering/index`
   * - :repo:`search-catalog <tree/main/examples/search-catalog>`
     - Faceted site search. ``DQuery[T]``, faceted filters, nested layouts, ``inherit_context=True``, cached search
     - :doc:`/content/topics/dependency-injection`, :doc:`/content/topics/context`
   * - :repo:`wiki <tree/main/examples/wiki>`
     - ``HybridRouterBackend``, ``router_manager.reload()`` on signal, DI, live search zone, forms with live Markdown preview
     - :doc:`/content/topics/file-router`, :doc:`/content/howto/write-a-router-backend`, :doc:`/content/topics/partial-rendering/index`
   * - :repo:`multi-tenant <tree/main/examples/multi-tenant>`
     - Tenant middleware, request-scoped static URLs, shared blocks via ``COMPONENT_BACKENDS`` ``DIRS``
     - :doc:`/content/howto/scope-requests-per-tenant`, :doc:`/content/topics/static-assets/backends`
   * - :repo:`kanban <tree/main/examples/kanban>`
     - Custom ``StaticBackend``, ``.jsx`` kind, ``DeepMergePolicy``, ``HashContentDedup``, composite components
     - :doc:`/content/topics/static-assets/asset-kinds`, :doc:`/content/topics/static-assets/deduplication`, :doc:`/content/topics/partial-rendering/framework-islands`
   * - :repo:`live-polls <tree/main/examples/live-polls>`
     - Framework SSE bridge, ``refresh`` patches, request-id echo, ``action_dispatched`` fan-out, Vue SFC asset kind
     - :doc:`/content/topics/partial-rendering/sse`, :doc:`/content/howto/stream-live-updates-with-sse`, :doc:`/content/topics/extending`, :doc:`/content/topics/partial-rendering/framework-islands`
   * - :repo:`observability <tree/main/examples/observability>`
     - Signal groups, custom ``ComponentsBackend``, ``DedupStrategy``, polling and lazy zones, custom patch verb, per-key ``JsContextSerializer``
     - :doc:`/content/topics/signals`, :doc:`/content/topics/extending`, :doc:`/content/topics/partial-rendering/index`
   * - :repo:`admin <tree/main/examples/admin>`
     - Django admin UI rebuilt on next.dj, request-aware form factories, server-opened modal layers, two page roots, middleware guard
     - :doc:`/content/howto/integrate-django-admin`, :doc:`/content/topics/multi-project`, :doc:`/content/topics/partial-rendering/index`

Shared assets
-------------

* :repo:`_shared <tree/main/examples/_shared>`. A shared component palette consumed through ``COMPONENT_BACKENDS`` ``DIRS``.
* :repo:`_template <tree/main/examples/_template>`. An empty scaffold to copy when starting a new example-shaped project.

See also
--------

.. seealso::

   :doc:`/content/intro/whatsnext` places these examples on the learning paths.
   :doc:`/content/topics/extending` maps extension mechanisms to sample projects.
