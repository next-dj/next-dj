.. _misc-examples:

Repository Examples
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
   * - `shortener <https://github.com/next-dj/next-dj/tree/main/examples/shortener>`__
     - File router, DI providers, LocMemCache, management command
     - :doc:`/content/topics/file-router`, :doc:`/content/topics/dependency-injection`
   * - `markdown-blog <https://github.com/next-dj/next-dj/tree/main/examples/markdown-blog>`__
     - Markdown posts, nested layouts, ``@context(serialize=True)``, context processor, co-located ``component.js``
     - :doc:`/content/topics/layouts`, :doc:`/content/topics/context`, :doc:`/content/topics/static-assets/js-context`
   * - `feature-flags <https://github.com/next-dj/next-dj/tree/main/examples/feature-flags>`__
     - Composite ``feature_guard``, signal receivers, cache invalidation
     - :doc:`/content/topics/components`, :doc:`/content/topics/signals`
   * - `audit-forms <https://github.com/next-dj/next-dj/tree/main/examples/audit-forms>`__
     - Compliance audit trail. ``FormWizard`` access-request flow, custom ``FormActionBackend``, ``action_dispatched`` / ``form_validation_failed``, dual audit channels
     - :doc:`/content/topics/forms/wizard`, :doc:`/content/topics/forms/backends`, :doc:`/content/topics/forms/signals`
   * - `search-catalog <https://github.com/next-dj/next-dj/tree/main/examples/search-catalog>`__
     - Faceted site search. ``DQuery[T]``, faceted filters, nested layouts, ``inherit_context=True``, cached search
     - :doc:`/content/topics/dependency-injection`, :doc:`/content/topics/context`
   * - `wiki <https://github.com/next-dj/next-dj/tree/main/examples/wiki>`__
     - ``HybridRouterBackend``, ``router_manager.reload()`` on signal, DI, forms with live Markdown preview
     - :doc:`/content/topics/file-router`, :doc:`/content/howto/write-a-router-backend`
   * - `multi-tenant <https://github.com/next-dj/next-dj/tree/main/examples/multi-tenant>`__
     - Tenant middleware, request-scoped static URLs, shared blocks via ``COMPONENT_BACKENDS`` ``DIRS``
     - :doc:`/content/howto/scope-requests-per-tenant`, :doc:`/content/topics/static-assets/backends`
   * - `kanban <https://github.com/next-dj/next-dj/tree/main/examples/kanban>`__
     - Custom ``StaticBackend``, ``.jsx`` kind, ``DeepMergePolicy``, ``HashContentDedup``, composite components
     - :doc:`/content/topics/static-assets/asset-kinds`, :doc:`/content/topics/static-assets/deduplication`
   * - `live-polls <https://github.com/next-dj/next-dj/tree/main/examples/live-polls>`__
     - Server-Sent Events broker, ``action_dispatched`` fan-out, Vue SFC asset kind, nested layouts
     - :doc:`/content/howto/stream-live-updates-with-sse`, :doc:`/content/topics/extending`
   * - `observability <https://github.com/next-dj/next-dj/tree/main/examples/observability>`__
     - Signal groups, custom ``ComponentsBackend``, ``DedupStrategy``, global and per-key ``JsContextSerializer``
     - :doc:`/content/topics/signals`, :doc:`/content/topics/extending`
   * - `admin <https://github.com/next-dj/next-dj/tree/main/examples/admin>`__
     - Django admin beside next.dj pages, request-aware form factories, two page roots, middleware guard
     - :doc:`/content/howto/integrate-django-admin`, :doc:`/content/topics/multi-project`

Shared assets
-------------

* `_shared <https://github.com/next-dj/next-dj/tree/main/examples/_shared>`__. A shared component palette consumed through ``COMPONENT_BACKENDS`` ``DIRS``.
* `_template <https://github.com/next-dj/next-dj/tree/main/examples/_template>`__. An empty scaffold to copy when starting a new example-shaped project.

See Also
--------

.. seealso::

   :doc:`/content/intro/whatsnext` places these examples on the learning paths.
   :doc:`/content/topics/extending` maps extension mechanisms to sample projects.
