.. _misc-examples:

Repository examples
===================

The ``examples/`` tree in the next.dj repository holds self-contained Django projects, each one a small finished product with its own README and its own end-to-end tests.
Each runs on SQLite and in-process ``LocMemCache``, with no Docker and no external service.
Tailwind loads from the Play CDN through a shared ``page_head`` component every root layout calls, so a machine with no network renders the pages unstyled.
That component renders the head through ``{% metadata %}``, and every example names itself in ``SITE["NAME"]``, declares its viewport and title template in ``METADATA["DEFAULTS"]``, and names its tab icon, a static ``icon.svg``, in the ``icons`` metadata key.
``manage.py check --deploy --tag seo`` passes clean in every example.
The ``kanban`` and ``live-polls`` folders add a Vite toolchain on top of that and need Node installed.
Every example renames ``PAGES_DIR`` and ``COMPONENTS_DIR`` on purpose and registers a project-level ``DIRS`` root in ``PAGE_BACKENDS``, so the settings show that neither directory name is fixed.
The :repo:`examples README <blob/main/examples/README.md>` carries the run commands and the conventions every folder follows.

Catalog
-------

Read ``shortener`` first, because it is written as a tour of the surface used most and the later examples build on it rather than repeat it.
The table runs roughly in order of how much it assumes.

.. list-table::
   :header-rows: 1
   :widths: 16 46 38

   * - Folder
     - What it builds
     - Primary docs
   * - :repo:`shortener <tree/main/examples/shortener>`
     - Shortens a URL, lists the results, and prepends a new row into the live list without a reload.
       Reads request values through custom DI providers, caches lookups in ``LocMemCache``, declares the post-submit redirect and flash message on the form itself, and seeds data from a management command.
       Serves a static ``robots.txt`` that keeps crawlers off the redirect URLs.
     - :doc:`/content/topics/file-router`, :doc:`/content/topics/dependency-injection`, :doc:`/content/topics/partial-rendering/index`, :doc:`/content/topics/seo/robots`
   * - :repo:`markdown-blog <tree/main/examples/markdown-blog>`
     - Publishes Markdown posts through nested layouts, hands serialised values to the browser, feeds a site-wide value through a context processor, and runs a co-located ``component.js`` beside the component that needs it.
       Builds the head from ``DEFAULTS`` down to a per-post ``@page.metadata`` callable with a plain ``BlogPosting`` JSON-LD dict and keywords from the front matter, renders ``{% breadcrumbs %}`` in its nested layouts, names a static social card in ``og.images``, and advertises an RSS feed through ``alternates.feeds``.
       Adds a dated sitemap and a ``robots.py``.
     - :doc:`/content/topics/layouts`, :doc:`/content/topics/context`, :doc:`/content/topics/static-assets/js-context`, :doc:`/content/topics/seo/metadata`, :doc:`/content/topics/seo/structured-data`, :doc:`/content/topics/seo/breadcrumbs`
   * - :repo:`feature-flags <tree/main/examples/feature-flags>`
     - Turns features on and off behind a composite guard component, and invalidates the flag cache from signal receivers rather than from the views that read it.
       Titles each panel with a one-line ``metadata`` dict and keeps the admin subtree ``noindex``.
     - :doc:`/content/topics/components`, :doc:`/content/topics/signals`
   * - :repo:`audit-forms <tree/main/examples/audit-forms>`
     - Records an audit trail of every submission through two independent channels, a custom form action backend and receivers on ``action_dispatched`` and ``form_validation_failed``.
       Runs a multi-step wizard inside a modal layer above a lazily loaded audit table.
       Titles its pages, keeps the wizard and the audit pages ``noindex``, and titles a request audit by its primary key through ``@page.metadata``, so no personal data reaches a tab title.
     - :doc:`/content/topics/forms/wizard`, :doc:`/content/topics/forms/backends`, :doc:`/content/topics/forms/signals`, :doc:`/content/topics/partial-rendering/index`
   * - :repo:`search-catalog <tree/main/examples/search-catalog>`
     - Filters a catalog from the query string with faceted filters that submit themselves and a list that extends as the visitor scrolls, across three levels of nested layouts sharing inherited context and a cached search.
       Carries one title template across the layouts, a self canonical that drops the filter query, a ``meta`` patch after a preset filter, and a ``sitemap.py`` over categories and products.
       Shows the marketing side as well, a category landing a CDN may cache, structured data on the product pages, third-party scripts in ``scripts.py`` behind a consent banner with hand-written vendor adapters, and ``{% #consented %}`` embeds.
     - :doc:`/content/topics/dependency-injection`, :doc:`/content/topics/context`, :doc:`/content/topics/seo/social-and-canonical`, :doc:`/content/topics/scripts/index`, :doc:`/content/howto/write-a-vendor-adapter`
   * - :repo:`wiki <tree/main/examples/wiki>`
     - Serves articles out of the database through a hybrid router that rebuilds its routes on a signal, with a search zone that answers as the visitor types and an editor that previews Markdown live.
       Titles and describes each article through a ``@page.metadata`` callable that reuses the row its context resolved, marks the editing pages ``noindex``, lists the articles through a lazy ``QuerySet`` sitemap dated per row, and keeps crawlers out of the search pages through a ``robots.py``.
     - :doc:`/content/topics/file-router`, :doc:`/content/howto/write-a-router-backend`, :doc:`/content/topics/partial-rendering/index`, :doc:`/content/topics/seo/metadata`, :doc:`/content/topics/seo/robots`
   * - :repo:`multi-tenant <tree/main/examples/multi-tenant>`
     - Resolves a tenant from a request header in middleware, rewrites every co-located asset URL per tenant on top of the staticfiles names it inherits, stamps a deploy build id into those URLs, and shares a header and footer across page roots.
       Makes the tenant the ``site_name`` and the default title of every workspace page through ``@page.metadata(inherit=True)``, which keeps each workspace ``noindex`` as well.
     - :doc:`/content/howto/scope-requests-per-tenant`, :doc:`/content/topics/static-assets/backends`, :doc:`/content/topics/seo/metadata`
   * - :repo:`kanban <tree/main/examples/kanban>`
     - Drives a board of React cards, teaching the pipeline a ``.jsx`` asset kind through a custom static backend, deep-merging serialised context across levels, and deduplicating co-located CSS by content hash.
     - :doc:`/content/topics/static-assets/asset-kinds`, :doc:`/content/topics/static-assets/deduplication`, :doc:`/content/topics/partial-rendering/framework-islands`
   * - :repo:`live-polls <tree/main/examples/live-polls>`
     - Streams poll results to every open browser over server-sent events, fanning out from a signal receiver that reads the bound form, with a locally bundled Vue single-file component subscribing through ``EventSource`` and a custom ``.vue`` asset kind.
       Titles the poll list with a ``metadata`` dict and each poll after its question through ``@page.metadata``.
     - :doc:`/content/topics/partial-rendering/sse`, :doc:`/content/howto/stream-live-updates-with-sse`, :doc:`/content/topics/extending`, :doc:`/content/topics/partial-rendering/framework-islands`
   * - :repo:`observability <tree/main/examples/observability>`
     - Watches a running project through one receiver per signal group, refreshing a dashboard from polling and lazy zones and a patch verb of its own, with a custom components backend, a custom deduplication strategy, a serializer swapped both globally and per decorator, and an opt-in hashed-manifest profile.
       Keeps the whole dashboard ``noindex`` from one root ``metadata`` dict and titles each stats page.
     - :doc:`/content/topics/signals`, :doc:`/content/topics/extending`, :doc:`/content/topics/partial-rendering/index`
   * - :repo:`admin <tree/main/examples/admin>`
     - Rebuilds the Django admin on next.dj over the stock ``ModelAdmin`` hooks, with request-aware form factories, guards on every mutating action, server-opened modal layers, keyed inline row forms, two page roots, and a middleware guard over the whole tree.
       Titles every model page, the changelist, the add, change, and delete forms, and the history, after its model through ``@page.metadata(inherit=True)``, and keeps the whole site out of search through ``SITE["INDEXABLE"] = False``, publishing no sitemap or robots file.
     - :doc:`/content/howto/integrate-django-admin`, :doc:`/content/topics/multi-project`, :doc:`/content/topics/partial-rendering/index`

Shared assets
-------------

* :repo:`_shared <tree/main/examples/_shared>`.
  A shadcn-inspired component palette every example consumes through ``COMPONENT_BACKENDS`` ``DIRS``, with its design tokens in one stylesheet.
* :repo:`_template <tree/main/examples/_template>`.
  The skeleton every example is copied from, which runs and passes its test while demonstrating nothing on its own.

See also
--------

.. seealso::

   :doc:`/content/intro/whatsnext` places these examples on the learning paths.
   :doc:`/content/topics/extending` maps extension mechanisms to sample projects.
