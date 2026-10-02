.. _internals-seo-pipeline:

SEO pipeline
============

This page covers the two halves of the seo machinery.
The first is the metadata of one page, from the ``page.py`` declarations to the head markup, the chain, fold, resolve, and render stages and the memos behind them.
The second is the crawler documents, how the seo area finds the sources at the top of every page root, how the sitemap backends turn them into sections, how a section stays lazy over a large table, how the routes join the lazy urlpatterns without a circular import, and what resets the memos.

.. contents::
   :local:
   :depth: 2

Metadata
--------

Four stages turn the declarations into tags, and each one sees only what it needs.

.. mermaid::

   flowchart LR
       Chain["chain: DEFAULTS, then each page.py"] --> Fold["fold: deep merge into Metadata"]
       Fold --> Resolve["resolve: request and SITE into ResolvedMetadata"]
       Resolve --> Render["render: markup only"]
       Resolve --> Header["X-Robots-Tag"]

The chain lists the sources of one page, root first, the settings tier of ``DEFAULTS`` and then the ``page.py`` of every directory from the page root down.
The fold merges them into ``Metadata`` without a request, which is what the system checks, the sitemap, and the response policy read.
The resolve stage reads the request and ``NEXT_FRAMEWORK["SITE"]``, makes every URL absolute, fills the self canonical and the hreflang alternates, applies the site indexability rule to the robots directives, derives the Open Graph fallbacks, and places a naive time in the current time zone.
The renderer receives the ``ResolvedMetadata`` alone, so a custom ``RENDERER`` cannot lose a ``noindex`` the site rule set.

The chain
~~~~~~~~~

``chain_entry`` walks the ancestors of the page inside its page tree, root first, and reads each ``page.py``.
A dict becomes a normalised ``Segment`` at once, a callable is kept as a ``ChainSource`` with an empty segment, and a file carrying both raises ``PageMetadataConflictError``.
An ancestor's callable joins the chain only when it was registered with ``inherit=True``, and the page's own callable always does.
Each segment records its directory below the tree root as its ``trail``, which the breadcrumbs read.
``Segment`` composes a ``Metadata`` with the fields only a segment carries, its source name, its title spec, its breadcrumb label, its trail, and the paths a ``Replace`` took whole.

Normalisation
~~~~~~~~~~~~~

``normalize.py`` compiles the accepted shapes once at import, by reflection over the annotations of ``MetadataDict``, ``SiteMetadataDict``, and every input dict they reach.
Each annotated key becomes a ``_Kind``, which names itself for a message, says cheaply whether a value fits, and coerces the value or fails with the key path.
A union becomes an ``_Either`` that coerces through the first option that fits, a ``Sequence`` an ``_Items``, a ``Literal`` a choice leaf, and a nested dict a ``_Block`` that builds its value object.
``RobotsDict`` reaches ``GooglebotDict`` under ``googlebot`` rather than itself, so the reflection never meets a cycle.

Four hand tables carry what an annotation cannot say.

- ``_LEAVES`` maps each scalar annotation, the ``Text`` and ``Url`` aliases among them, to its leaf shape.
- ``_BUILDS`` maps each input dict to the value it builds, a marker class or a small builder such as ``_icons`` that flattens the icon groups.
  The page dicts build a plain dict, which ``_segment`` splits into the segment fields and the ``Metadata``.
- ``_SHORTHANDS`` names the key a bare string stands for beside a dict, the ``url`` of an image or the ``text`` of a title.
- ``_PATH_KINDS`` holds the three keys whose shape depends on the value rather than the annotation, ``canonical``, ``jsonld``, and ``alternates.languages``.
  A shape a path names stands alone, so it fits any value and refuses a wrong one as it coerces.

A new key of an input dict needs no table entry unless it is a new scalar, a new dict, or a shorthand.

The fold
~~~~~~~~

The entry stores three folds.
The prefix is the fold of the settings tier and every source up to the first callable, so a request folds only the tail.
The static fold treats every callable as empty, and the checks, the sitemap, and the response policy read it without a request.
The whole fold is kept when no source is a callable, and a request answers with it directly.

The request-time fold replays the tail over the prefix.
A callable source is resolved through the dependency resolver with the request, the URL kwargs, the dependency cache of the render, and the render context, and the mapping it returns is normalised with the callable and its file as the source name, so a shape error names the function rather than the page.
``fold_segment`` merges each segment by the strategy its fields declare, deep for a nested block, by name for ``other`` and ``properties``, by ``@id`` for ``jsonld``, and whole for everything else, a ``Replace`` taking its path whole, see :doc:`/content/topics/seo/merge` for the table.
The top level, a nested block, and ``merge_segments`` all merge field by field through one ``_merge_values``, and the chain builds its prefix and its static fold through ``fold_segments``.
The ``@id`` a node is merged by passes through ``node_id``, so ``#org`` and ``/#org`` name one node in the fold, in ``next.W108``, and in ``next.E101``.

``fold_metadata`` with an ``overlay`` replaces the page's own callable with the given segment, laid over the page's own dict, and refolds from the settings tier.
An inherited callable of an ancestor runs as it does in the render, against a context the caller's factory builds only at that point, which is how ``Patches.meta`` runs the guard of the origin page and builds its render context on demand.

Freshness
~~~~~~~~~

The chain of a page is memoised per path.
An entry is current while the module version of ``next.pages.loaders`` has not moved, the settings tier is the same object, the ``page.py`` files along the path carry the stamps they were loaded at, and the registry stamps of their callables are unchanged.
The module version moves only when every memo is dropped, while a single re-executed ``page.py`` moves its own stamp, so an edit rebuilds the chains that pass through that file and no other.
A ``@page.metadata`` registration that changes its callable or its ``inherit`` flag moves its registry stamp, and a settings reload rebuilds the settings tier.

Failures
~~~~~~~~

A ``@page.metadata`` callable is user code that runs on every request, so the render contains what it raises through the ``FailureLog`` of ``next.diagnostics``.
``MetadataThunk.fold`` leaves a callable that raises, or returns a shape the schema refuses, out of the fold and logs it once per file and exception type.
:exc:`~django.http.Http404` and :exc:`~django.core.exceptions.PermissionDenied` pass through, and under ``DEBUG`` or ``STRICT_LOADING`` every failure raises with a note naming the callable and its file.
A chain the schema refuses as a whole, a shape error in a ``page.py`` dict or a file declaring both forms, folds to the settings tier alone.
``fold_metadata``, the call ``Patches.meta`` makes, does not contain, so the caller decides what a failure costs, and ``meta()`` drops its own operation.

The resolve contains two more failures that only a render can meet.
A lazy URL forced to a scheme outside http and https leaves out the tag carrying it, and the whole hreflang set when it is one of the alternates, since the pairs answer one another.
A JSON-LD node holding a value JSON cannot write is left out of the graph.

JSON-LD values
~~~~~~~~~~~~~~

``ld.to_json`` is the one walk from a node or a raw value to its JSON form.
It renders a nested ``Node`` or ``Ref`` in place, passes every ``@id`` through the ``ids`` map and the strings of a URL property through ``urls``, writes an enum as its value, places a naive datetime in the current time zone, and raises ``ValueError`` for a leaf JSON cannot hold.
``ld.iter_json`` yields every value of a raw value with its key path, which ``normalize.py`` reads to refuse a bad leaf or key with its path at load time.
``backends.dump_jsonld`` writes the JSON of one script, ``allow_nan=False`` and the characters that could close it escaped, and ``next.E127`` serialises a declared node through ``to_json`` and ``dump_jsonld`` with the same ``@id`` map, so the check and the renderer cannot disagree.

The render
~~~~~~~~~~

The render context carries a ``MetadataThunk`` under a reserved key, holding the registry, the page path, the request, the URL kwargs, and the dependency cache of the render.
It keeps no reference to the context and does nothing until ``{% metadata %}`` or ``{% breadcrumbs %}`` reads it.
The tag hands the thunk the flattened context it renders in, so a value a zone override or a ``{% with %}`` block puts in scope reaches the callables, and a template without either tag runs no callable at all.
The resolve is memoised in the root layer of the template's render context, which an ``{% include %}`` shares, so the two tags and every included template share one resolve.
The ``ResolvedMetadata`` is published on the request, and the response layer repeats its blocking robots directives as ``X-Robots-Tag``.

``resolve_metadata`` reads ``site_url`` and ``site_indexable`` from ``next.site``, makes every URL absolute, fills the self canonical and the translated hreflang alternates, derives the Open Graph fallbacks and locale, places naive times in the current time zone, and appends the ``BreadcrumbList`` node when the page has at least two crumbs.
``ResolvedMetadata.crumbs`` holds a lazy ``Breadcrumbs``, and its ``breadcrumbs`` property reverses the crumb URLs on the first read under the URLconf of the request.
The URL name and the parameter names of a crumb's route are memoised per ``trail`` and ``URL_NAME_TEMPLATE``, so a warm render parses no route and reverses only the crumbs it shows.

``metadata_renderer`` builds the class ``NEXT_FRAMEWORK["METADATA"]["RENDERER"]`` names once, through ``resolve_setting_class``, and again on ``settings_reloaded``.
A path that does not resolve, or a constructor that raises, leaves the render on ``HtmlMetadataRenderer`` with one logged error, and ``next.E107`` resolves the same path at check time without building the class.
``HtmlMetadataRenderer`` walks its ``sections`` in order and joins what each ``render_<name>`` hook answers, which is the seam a subclass reorders or extends, see :doc:`/content/ref/metadata`.

Crawler documents
-----------------

Components
~~~~~~~~~~

.. mermaid::

   flowchart LR
       Router["next.urls router_manager"] --> Discovery["discovery<br/>page_tree_roots"]
       Discovery -- "load_tree_source" --> Registry["registry<br/>sitemap_items_registry"]
       Discovery --> Manager["manager<br/>seo_manager"]
       Manager --> Backends["backends<br/>PageTreeSitemapBackend and others"]
       Backends --> Sitemaps["sitemaps<br/>PageTreeSitemap, ChainedEntries"]
       Registry --> Sitemaps
       Manager --> Sources["robots sources"]
       Manager --> Ports["ports<br/>SeoRoutesImpl"]
       Ports --> Slot["next.ports<br/>seo_routes_slot"]
       Slot --> Lazy["next.urls.manager<br/>_LazyUrlPatterns"]
       Lazy --> Views["views<br/>sitemap, robots"]
       Views --> Sitemaps
       Views --> Sources

Discovery
~~~~~~~~~

``page_tree_roots`` walks the backends of the router manager once per reset and, for each page root a backend reports, probes the three source names at the top of the tree, ``sitemap.py``, ``robots.py``, and ``robots.txt``.
A root two backends report is visited once, and the roots keep router order, which is the order the source selection and the sitemap index follow.
Each root becomes a ``SeoRoot`` carrying the page root, its label and section, its routed trails walked once, the two Python sources, the path of the static ``robots.txt``, and the stamps of all three.

``load_source`` executes a Python source through ``load_tree_source`` of ``next.utils``, the loader ``scripts.py`` goes through as well, outside the page module memo, after forgetting what the file registered before, so a re-executed ``sitemap.py`` registers its callables afresh.
The answer is a ``TreeSource`` carrying the module or the failure and the modification time the file ran at.
A source that raises is logged once and keeps a ``SeoSourceImportError`` on its ``SeoSource`` for the checks, and its route answers 404 through a ``BrokenSource`` rather than falling back to another source.
The page module memo and its failure record never see an SEO source, so a broken ``robots.py`` leaves ``has_load_errors`` of the pages untouched.

The section of a root is the ``section`` its ``sitemap.py`` declares, else the label of the innermost installed application holding the root, else the slugified directory, and ``unique_labels`` hands out the lowest free ``-N`` suffix to a repeat.

Registry
~~~~~~~~

``sitemap.items(trail)`` reads the file running the decorator through ``registering_file`` and registers a ``SitemapItemsEntry`` of that file, the trail, the callable, and its ``section``, ``kwargs``, and ``lastmod`` options.
The registry keeps an ordered list and an index keyed by ``(file, trail)``, the later binding of one key replacing the earlier and a repeat within one execution recorded as a conflict for ``next.E113``.
Every write moves its version and sends ``sitemap_items_registered``.

Manager and backends
~~~~~~~~~~~~~~~~~~~~

``seo_manager`` loads the ``SITEMAP_BACKENDS`` through the shared ``load_backends`` loader, which sends ``sitemap_backend_loaded`` per entry, and memoises the selected robots source.
``sections(request)`` answers nothing on a site closed to search, save one only ``DEBUG`` closes, and otherwise merges the sections of every backend in order, the first holder of a name keeping it.
``fingerprint`` hashes the bytes of every source file, the backend entries, and the site config, and prefixes the ``cache_page`` keys with it, so an edited source never answers from a stale copy.
The entries and the config are spelled through ``stable_repr``, sorted collections, a callable as its dotted name, and a lazy string as its text, so every worker computes the same key.
Its ``version`` reads ``seo_routes_version`` of ``next.urls.manager``, which every reset moves, and keys the cached views and the route filter.
``cache_control`` settles the caches of several backends through ``shortest_cache``, keyed on whether a cache may store at all and then on the shortest age.

``PageTreeSitemapBackend`` answers one ``PageTreeSitemap`` per root with a ``sitemap.py``, plus one per ``section=`` its items name, and drops the items of a trail an ``exclude`` glob covers.
The static trails of a root are listed through ``listed_trails``, which leaves out a dynamic trail, an excluded one, and one whose static fold is ``noindex``, and the list is memoised against the module version of ``next.pages.loaders``.
A trail an items callable claims is left out of the static list, so the callable is the only source of its URLs.

Building a section
~~~~~~~~~~~~~~~~~~

``PageTreeSitemap`` reads its Django attributes once through ``SitemapOptions.read``, the single reader the checks share, which takes a wrong shape as unset and caps ``limit`` at 50000.
Its items are a ``ChainedEntries`` of ``Part`` values, the static trails first and then one part per items callable, each callable resolved through the dependency resolver with the request when there is one.
A ``QuerySet`` answer stays a query, ordered by primary key when unordered, a sequence stays as it is, and an iterator is read into a list.

The paginator is the public ``paginator`` property of the Django sitemap, overridden to paginate the chained sequence rather than a list.
``count`` sums the count of every part, a ``QuerySet`` answering with one ``COUNT``, and a page slice maps onto the parts it spans and slices each, so a page of a large table reads one ``LIMIT`` and ``OFFSET`` query.
Under ``i18n`` a ``LanguagePairs`` sequence pairs every entry with every language, and its count is the product.
``get_latest_lastmod`` answers a ``QuerySet`` part with one ``Max`` aggregate on the ``lastmod`` column, and a materialised part with the newest date it holds.

``location`` reverses through ``page_reverse`` per call, so the language active in Django's ``i18n`` loop lands in the path.
Without ``i18n``, ``get_urls`` runs under ``LANGUAGE_CODE``, so the document never depends on the language of the crawler.
The Django ``x_default`` derivation is switched off, and ``get_urls`` appends an ``x-default`` alternate on the URL of the default language through the same ``x_default_url`` helper the head uses.
``get_domain`` reads ``request_origin``, ``SITE["URL"]`` first, then :func:`~django.contrib.sites.shortcuts.get_current_site`, then the request host.

The URL slot
~~~~~~~~~~~~

``next.seo`` imports ``next.urls`` for the router manager and the reverse helper, so ``next.urls.manager`` cannot import the routes back.
``SeoRoutes`` in ``next.ports`` declares ``patterns``, ``SeoRoutesImpl`` in ``next.seo.ports`` implements it over the manager, and ``NextFrameworkConfig.ready()`` binds the implementation into ``seo_routes_slot``.
``_LazyUrlPatterns`` concatenates the router patterns, the form-action patterns, the ``/_next/csrf/`` route, and the slot's patterns, and its ``version_token`` is the triple of the router version, the form-action version, and ``seo_routes_version``.
``seo_routes_version`` is a ``SeoRoutesVersion`` in ``next.urls.manager``, moved by ``NextFrameworkConfig.ready()`` right after it binds the slot and by the seo manager on every reset, so a resolve reads the token as a plain attribute and the port carries no version.
``patterns`` answers only the routes whose source exists, so an empty answer leaves a project's own pattern below ``include("next.urls")`` in charge of that address.
``next.seo.urls`` serves the same routes through ``SeoPatterns``, a lazy sequence refiltered whenever the manager version moves.

Views
~~~~~

Every view answers ``GET`` and ``HEAD`` alone, turns an ``Http404`` into a plain-text 404 that skips the project's 404 handler, and stamps ``X-Robots-Tag: noindex, nofollow`` on a site closed to search.
A view reached through a mount other than ``next_seo`` while ``next.seo.urls`` serves that route at another address answers 404, so each document has one address.
The sitemap and robots views sit behind a single-slot wrapper that applies the declared ``Cache-Control`` and, for a storable cache with an age, :func:`~django.views.decorators.cache.cache_page` keyed on the fingerprint, and rebuilds the wrap when the manager version moves.

The sitemap view answers the only section as a urlset, or the index once there are several sections or a section paginates, rendering the templates of ``django.contrib.sitemaps`` with Django's ``SitemapIndexItem``.
The robots view answers the fixed open document on a closed site, the bytes of a static file, or the rendered rules with the project's own ``Sitemap:`` line first.
A site only ``DEBUG`` closes, ``debug_closed()`` of ``next.site``, still answers its sections and its own robots, and the closed-site stamp marks both ``noindex, nofollow``.
A static file read that fails answers the last good bytes, or a 503 with ``Retry-After`` when there are none.

Resets
~~~~~~

``seo_manager.reset`` drops the backends, the discovered roots, the selected sources, and the fingerprint, moves the version, and clears Django's URL caches, since the route set may change with the sources.

- ``forget_seo_sources``, the public receiver of ``next.seo.manager``, resets it on ``router_reloaded``, connected in ``ready()``, since a reload can change which roots exist.
- The same receiver resets it on ``settings_reloaded``, since the backends, the site config, and the fingerprint read settings.
- ``seo_manager.refresh()`` resets it while ``DEBUG`` watches template edits and a source file of any root appeared, went, or moved on disk, and every SEO view asks it first, so an edited source shows without a restart.
- ``reset_check_caches`` and ``next.testing.reset_seo`` call ``reset_seo_sources``, which also drops the items registry, so a check run or a test after an in-place edit executes the sources again.

Autoreload
~~~~~~~~~~

The development watcher leaves the tree-top sources out of its specs, ``sitemap.py``, ``robots.py``, ``robots.txt``, and ``scripts.py`` alike.
``seo_manager.refresh()`` and the mtime a ``TextFile`` holds reload them in process on the next request, so an edit to one of them never restarts ``runserver``.
See :doc:`autoreload` for the spec list and the reload decisions.

Checks
~~~~~~

``loaded_seo_roots`` in ``next.seo.checks.roots`` runs the same discovery the routes run and keeps it in a ``RunMemo``, so the checks of one run share a single discovery and read the sources, the modules, and the import errors the runtime loads.
The checks call the helpers the routes call, ``SitemapOptions``, ``listed_trails``, and ``is_excluded`` for the sitemap and ``declared_rules`` and ``robots_candidates`` for robots, rather than restating them.
See :doc:`/content/ref/seo` for the check list and :doc:`/content/ref/system-checks` for the codes.

See also
--------

.. seealso::

   :doc:`/content/topics/seo/sitemaps`, :doc:`/content/topics/seo/sitemap-sections`, and :doc:`/content/topics/seo/robots` for the project-facing behaviour.
   :doc:`url-router` for the lazy pattern sequence the routes join.
   :doc:`page-discovery` for the ``page.py`` loading the metadata chain reads.
   :doc:`/content/topics/seo/merge` for the merge rule of every key.
   :doc:`/content/ref/ports` for the slot contract.
