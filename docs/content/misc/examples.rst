.. _misc-examples:

Repository examples
===================

The ``examples/`` tree in the next.dj repository holds self-contained Django projects, each one a small finished product with its own README and its own end-to-end tests.
Each runs on SQLite and in-process ``LocMemCache``, with no Docker and no external service.
Tailwind loads from the Play CDN through a shared ``page_head`` component every root layout calls, so a machine with no network renders the pages unstyled.
That component renders the head through ``{% metadata %}``, and every example names itself in ``SITE["NAME"]``, declares its viewport and title template in ``METADATA["DEFAULTS"]``, and names its tab icon, a static ``icon.svg``, in the ``icons`` metadata key.
``manage.py check --deploy --tag seo`` passes clean in every example.
The ``kanban`` and ``live-polls`` folders add a Vite toolchain on top of that and need Node installed.
Every application keeps its pages in ``pages/`` and its page-tree components in ``pages/_components/``, and every example registers a project-level ``DIRS`` root in ``PAGE_BACKENDS``.
The ``admin`` example also serves the components at the root of its ``shadcn_admin`` application through ``COMPONENT_BACKENDS`` ``DIRS``.
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
       Serves a print view of each post under a root layout that cuts the site shell.
     - :doc:`/content/topics/layouts`, :doc:`/content/howto/give-a-section-its-own-document-shell`, :doc:`/content/topics/context`, :doc:`/content/topics/static-assets/js-context`, :doc:`/content/topics/seo/metadata`, :doc:`/content/topics/seo/structured-data`, :doc:`/content/topics/seo/breadcrumbs`
   * - :repo:`feature-flags <tree/main/examples/feature-flags>`
     - Turns features on and off behind a composite guard component, and invalidates the flag cache from signal receivers rather than from the views that read it.
       Titles each panel with a one-line ``metadata`` dict and keeps the admin subtree ``noindex``.
     - :doc:`/content/topics/components`, :doc:`/content/topics/signals`
   * - :repo:`audit-forms <tree/main/examples/audit-forms>`
     - Records an audit trail of every submission through two independent channels, a custom form action backend and receivers on ``action_dispatched`` and ``form_validation_failed``.
       Runs a multi-step wizard inside a modal layer above a lazily loaded audit table.
       Opens three intercepting routes as modals over the real pages, a ``(.)request/[int:access_request]/audit/`` peek with a polling zone inside the modal, a ``(...)`` peek of the same request from ``/admin/audit/``, and a receipt modal over a target whose body is a ``render()`` function.
       Titles its pages, keeps the wizard and the audit pages ``noindex``, and titles a request audit by its primary key through ``@page.metadata``, so no personal data reaches a tab title.
     - :doc:`/content/topics/forms/wizard`, :doc:`/content/topics/forms/backends`, :doc:`/content/topics/forms/signals`, :doc:`/content/topics/partial-rendering/index`, :ref:`topics-file-router-intercepts`
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
       Makes the tenant the ``scope`` dependency, so the editor page loads its note through ``DObject`` and ``visible_to``.
     - :doc:`/content/howto/scope-requests-per-tenant`, :doc:`/content/topics/dependency-injection`, :doc:`/content/topics/static-assets/backends`, :doc:`/content/topics/seo/metadata`
   * - :repo:`kanban <tree/main/examples/kanban>`
     - Drives a board of React cards, teaching the pipeline a ``.jsx`` asset kind through a custom static backend, deep-merging serialised context across levels, and deduplicating co-located CSS by content hash.
     - :doc:`/content/topics/static-assets/asset-kinds`, :doc:`/content/topics/static-assets/deduplication`, :doc:`/content/topics/partial-rendering/framework-islands`
   * - :repo:`live-polls <tree/main/examples/live-polls>`
     - Streams poll results to every open browser over SSE by default, with optional WebSocket delivery through Channels under ASGI, fanning out from a signal receiver that reads the bound form.
       A staff host opens or closes polls and sets a deadline through Django admin.
       Server-side form validation enforces the deadline, and the live countdown uses the stream's estimated server clock.
       The runtime opens the stream from ``data-next-sse``.
       The locally bundled Vue component listens for context and topic events and uses a custom ``.vue`` asset kind.
       Titles the poll list with a ``metadata`` dict and each poll after its question through ``@page.metadata``.
       Serves each poll as an embeddable widget under a root layout with a document shell of its own.
     - :doc:`/content/topics/realtime`, :doc:`/content/howto/stream-live-updates-with-sse`, :doc:`/content/howto/stream-over-websockets-with-channels`, :doc:`/content/howto/count-down-to-a-server-deadline`, :doc:`/content/topics/extending`, :doc:`/content/topics/partial-rendering/framework-islands`
   * - :repo:`observability <tree/main/examples/observability>`
     - Watches a running project through one receiver per signal group, refreshing a dashboard from polling and lazy zones and a patch verb of its own, with a custom components backend, a custom deduplication strategy, a serializer swapped both globally and per decorator, and an opt-in hashed-manifest profile.
       Keeps the whole dashboard ``noindex`` from one root ``metadata`` dict and titles each stats page.
     - :doc:`/content/topics/signals`, :doc:`/content/topics/extending`, :doc:`/content/topics/partial-rendering/index`
   * - :repo:`admin <tree/main/examples/admin>`
     - Rebuilds the Django admin on next.dj over the stock ``ModelAdmin`` hooks, with request-aware form factories, guards on every mutating action, server-opened modal layers, keyed inline row forms, and several page trees under ``NEXT_FRAMEWORK["LOGIN_REQUIRED"] = True``.
       Each guarded tree declares its staff login and permission with ``inherit=True`` in its root ``page.py``, and the roots of ``admin_audit`` and ``library`` serve no page of their own and carry only those declarations and the ``Dashboard`` crumb, while the login and logout pages open through ``page.public()``.
       Each application keeps one ``pages/`` tree under the shared ``include("next.urls")`` at ``admin/``, so the ``library`` outline answers under ``/admin/library/book/<int:book>/outline/``, and the manuscript page beside it checks ``library.change_book`` on the book itself through ``obj=DObject[Book]``.
       The crumbs of each page come from its own tree, so the activity feed reads ``Dashboard › Activity`` and the outline ``Dashboard › Outline``.
       Titles every model page, the changelist, the add, change, and delete forms, and the history, after its model through ``@page.metadata(inherit=True)``, and keeps the whole site out of search through ``SITE["INDEXABLE"] = False``, publishing no sitemap or robots file.
       Its ``admin_audit`` application serves its activity feed from its own ``pages/activity/`` and plugs its sidebar link and dashboard panel into ``shadcn_admin`` from its ``admin.py`` through ``register_nav`` and ``register_panel``, a registry in ``shadcn_admin/services.py`` that works the way ``admin.register`` does, so the base never imports the plugin.
       Splits the pages of ``shadcn_admin`` into the route groups ``(auth)`` and ``(app)``, which add nothing to any URL, and gives ``(auth)`` a root layout that opens with ``{% layout root %}`` and cuts the admin chrome out of the login and logout pages.
       Opens a quick-edit modal through the intercept ``(.)[int:pk]/change/`` over the changelist with the change form inside it, a ``(..)history/`` peek of the latest entries of a row, and a manuscript peek in ``library/pages/library/book/[int:book]/(.)manuscript/`` whose target and intercept each check an object permission.
       Its object permissions in ``permissions.py`` are plain functions that combine their conditions with ``and`` and ``or``, and the seeded ``editor`` account shows them narrowing a staff user.
     - :doc:`/content/howto/integrate-django-admin`, :doc:`/content/topics/multi-project`, :doc:`/content/topics/partial-rendering/index`, :doc:`/content/topics/file-router`, :ref:`topics-file-router-route-groups`, :ref:`topics-file-router-intercepts`, :doc:`/content/topics/access`, :doc:`/content/topics/permissions`

Shared assets
-------------

* :repo:`_shared <tree/main/examples/_shared>`.
  A shadcn-inspired component palette every example consumes through ``COMPONENT_BACKENDS`` ``DIRS``, with its design tokens in one stylesheet.
* :repo:`_template <tree/main/examples/_template>`.
  The skeleton every example is copied from, Django's own ``startproject`` and ``startapp`` output with the canonical next.dj files, the shared UI kit, and the test scaffold.
  Its application tree answers under ``/myapp/`` through ``path("myapp/", pages("myapp"))``, and ``LoginRequiredMiddleware`` closes its pages and the Django admin beside them.
  It runs and passes its tests while demonstrating nothing beyond the starter, and its README names the commands that produce it, see :ref:`intro-install-project`.

See also
--------

.. seealso::

   :doc:`/content/intro/whatsnext` places these examples on the learning paths.
   :doc:`/content/topics/extending` maps extension mechanisms to sample projects.
