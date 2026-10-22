.. _internals-url-router:

URL router
==========

The file router scans the filesystem, builds URL patterns, and reloads at runtime.

.. contents::
   :local:
   :depth: 2

Overview
--------

The URL subsystem owns the file router, the filesystem walker that discovers page modules, and the reverse helpers.
It listens on the Django URL resolver through ``include("next.urls")`` and produces patterns from the filesystem layout.

Pipeline
--------

.. mermaid::

   flowchart TB
       Walk[Filesystem walk] --> Parser[Parser]
       Parser --> Patterns[URL patterns]
       Patterns --> Manager[RouterManager]
       Manager --> Trie[TrieURLResolver]
       Resolver[Django URL resolver] --> Trie
       Trie --> PageView[Page view]
       Manager -- reload --> Reload[Rebuild patterns]
       Reload --> Patterns

Modules
-------

``next.urls.backends``.
   ``RouterBackend`` is the abstract contract.
   Its concrete ``page_roots``, ``components_folder_name``, and ``skip_dir_names`` methods form the route introspection contract described below.
   ``FileRouterBackend`` implements file based routing.
   ``RouterFactory`` builds the router one entry names, for the callers that build one at a time.

``next.urls.parser``.
   Turns directory names into URL patterns.
   Recognises ``[name]``, ``[type:name]``, and ``[[name]]`` shapes.
   The ``default_url_parser.parse_url_pattern`` method is the single source of bracket conversion, used by both the router and the system checks, so a route and its check always agree on the resulting Django path string.

``next.urls.manager``.
   ``RouterManager`` builds the active pattern list, exposes ``reload``, and emits the ``router_reloaded`` signal.
   The module-level ``urlpatterns`` is a one-element sequence holding the resolver named by ``URL_RESOLVER``, which ships as ``TrieURLResolver`` and wraps the lazy router and form-action pattern sequence.
   It is a ``Sequence`` rather than a ``list``, so a root URLconf hands it to ``include()`` and never concatenates or appends to it.

``next.urls.resolver``.
   ``TrieURLResolver`` narrows each ``resolve()`` call to a few candidates through a static route map and a segment trie, with the inherited linear scan as fallback.

``next.urls.dispatcher``.
   ``FilesystemTreeDispatcher`` walks the pages directory tree and yields ``(url_path, page_file)`` pairs that the router turns into URL patterns.
   The ``url_path`` is the group-free URL trail, see `Route groups and intercepting routes`_ below.
   The module-level helper ``scan_pages_tree`` instantiates the dispatcher with the configured skip set and returns the same pairs as an iterator.
   The walk itself is ``next.utils.walk_page_tree``, which sits below both this package and the checks layer because the system checks run the same walk over the trees a backend reports.
   The dispatcher adds the one thing the checks do not want, a callback on each skipped directory that registers the component folders the router passes, under the raw file-system trail of the directory that holds them.
   It forwards an ``on_intercept`` callback to the walk, which the file router uses to collect the intercepting pages.

``next.urls.markers``.
   Hosts the ``DUrl`` and ``DQuery`` annotation markers, the four request/URL/query parameter providers, and the ``get_multi_values`` helper.
   See :doc:`/content/topics/dependency-injection` for the marker semantics and the provider order.

``next.urls.reverse``.
   ``page_reverse``, ``page_reverse_lazy``, and ``with_query`` helpers.

``next.urls.urlconf``.
   Walks the root URLconf for the route ``next.urls`` is included under and for every ``pages()`` include, as `Trees placed by pages()`_ below describes.

``pages()`` and ``MountedTree``.
   The lazy include target that holds one application tree, in the module ``next.urls`` re-exports ``pages`` from.

``next.urls.routes``.
   ``collect_routes`` and ``Route``, the route table that ``manage.py routes`` prints.
   ``with_intercepts(routes)`` returns the table with one extra row after each routed page that has intercepting pages, and ``Route.intercept`` holds the rule of such a row and is ``None`` on a routed one.

``next.intercept``.
   Holds ``Intercept``, ``InterceptTable``, the process-wide ``intercepts`` table, and the ``forget_intercepts`` receiver, see `Route groups and intercepting routes`_ below.

URL name computation
--------------------

Names follow ``next:page_<segments>`` where the segments come from the directory path.

- Static segments contribute their directory name unchanged.
- Captured segments contribute the raw bracket text with separators collapsed to underscores, so a converter prefix such as ``int:`` stays in the name.
- Wildcard segments contribute the parameter name without brackets.

The template ``URL_NAME_TEMPLATE`` controls the format.
The default ``page_{name}`` produces the names listed in :doc:`/content/topics/file-router`.

The name computation collapses ``/``, brackets, ``:``, ``-``, and ``_`` into a single underscore, so distinct routes such as ``foo-bar`` and ``foo_bar`` can produce the same name.
The ``check_reverse_name_collisions`` system check walks every page tree of every router, computes the reverse name of each route through the same parser, and fails with ``next.E039`` when two distinct routes collapse to one name within one namespace, listing the conflicting paths.

A ``pages()`` tree is named from its tree-relative trail in a namespace of its own.

Resolution algorithm
--------------------

``include("next.urls")`` hands Django a single ``TrieURLResolver`` that wraps the lazy sequence of CSRF, form-action, router, and SEO patterns, in that order.
The SEO source is read through the ``SeoRoutes`` port, because ``next.seo`` imports ``next.urls`` and the manager cannot import it back, and it holds the sitemap routes only while the project serves a sitemap and the robots route only while its source exists at the top of a page root, see :doc:`seo-pipeline`.
The sequence caches the concatenated pattern list against a three-part version token, one counter bumped by ``router_manager.reload()``, one bumped when form actions register or clear through the form-action manager, and ``seo_routes_version`` for the SEO routes.
``seo_routes_version`` lives in ``next.urls.manager``, ``NextFrameworkConfig.ready()`` moves it right after it binds the SEO port and the seo manager moves it on every reset of its sources, so the token is read as a plain attribute and never through the port.
A sequence read before the port is bound leaves the seo routes out and caches nothing.
The counters are read after the pattern build, because expanding page modules can register form actions mid-build, so the cache stays valid for the post-registration state.
A registration that writes into a backend directly, bypassing the manager, is not tracked.

The resolver builds a route index from the same sequence and versions it with the same token.

- A route without parameters lands in a static map keyed by the full route string, so a static hit is one dictionary lookup.
- A parameterised route becomes a path through a segment trie.
  A literal segment becomes a literal edge, and a segment whose converters all match within one segment, ``int``, ``str``, ``slug``, or ``uuid``, becomes a param edge.
- A segment that can span slashes, such as ``<path:...>`` or a custom converter, stops indexing at its node, and the candidate matches any remaining tail from there.
- A pattern that is not a route-string pattern, such as one built with :func:`~django.urls.re_path`, is never indexed and joins the candidate list of every lookup.

``resolve()`` collects candidates from the static map and a depth-first walk over the trie, following the literal edge before the param edge at every node and picking up tail candidates along the way.
The candidates are sorted by their original position in the pattern list and tried with the standard ``pattern.resolve()``, so overlapping routes keep Django's first-match-wins rule and converters run unchanged.
When no candidate matches, the resolver falls back to the inherited linear scan, which raises the canonical ``Resolver404`` with the complete ``tried`` list.
The fallback also covers any pattern the trie could not index.

On a successful match ``ResolverMatch.tried`` contains only the candidates that were actually tried, not every pattern that precedes the winner in the list.
A 404 goes through the fallback, so its ``tried`` is identical to the linear scan.

Setting ``URL_RESOLVER`` in ``NEXT_FRAMEWORK`` to ``"django.urls.resolvers.URLResolver"`` replaces the trie resolver with the stock Django class and routes every call through the plain linear scan.
The resolver is rebuilt on settings reload, so the swap takes effect without a restart.

Route order
-----------

The lazy sequence lists the framework routes first, the CSRF token endpoint at ``_next/csrf/`` and the form action endpoint at ``_next/form/<uid>/``, then the page routes, then the SEO routes.
A page tree with a root catch-all such as ``[[rest]]`` matches every path, so a framework route listed after it would never answer, and every deferred token fetch and form post would reach that page instead.
The ``next.E149`` system check resolves both addresses and compares the resolved view with the framework view, so a pattern of the root URLconf listed above the include is caught even when it reuses the framework URL name.

Inside one page tree the walker yields the page of a directory first, real or virtual, then the pages of the route groups inside it, and then descends into the routed subdirectories.
Those subdirectories are the directory's own and those of every group inside it, ranked together by kind, static names first, then names holding a ``[param]`` segment, then names holding a ``[[catch-all]]`` segment.
Within a kind, a name with more literal text comes first, so ``post-[id]`` precedes ``[slug]``, then the narrower converters, ``int`` and ``uuid`` ahead of ``slug``, ``slug`` and a project converter ahead of ``str``, and ``str`` ahead of ``path``, and the name decides the rest.
The rank reads a name through ``ROUTE_BRACKET_PATTERN``, the expression the URL parser converts, so ``blog/about`` always precedes ``blog/[slug]``.
The ranking is stable, so a child of the directory itself stays ahead of a child of a group when both rank equally.
The pattern list is therefore the same on every file system, whatever order ``os.scandir`` returns the entries in.

Across trees the page routes list the application trees in ``INSTALLED_APPS`` order, then the ``DIRS`` roots.
A ``pages()`` tree sits outside this list, where its URLconf includes it.

A trail the parser refuses, such as a repeated parameter name, does not abort the build.
``FileRouterBackend`` logs a warning naming the ``page.py``, leaves that page unrouted, and routes the rest, so ``manage.py check`` runs and reports it.

Route groups and intercepting routes
------------------------------------

One walk of a page tree classifies each subdirectory of a directory by its name, through a single function, into four kinds.
A routed directory contributes a URL segment and is walked.
A group, a name such as ``(auth)``, contributes nothing to the URL or to the URL name.
An intercept, a name that starts with ``(.``, is walked on its own and never routed, and a malformed one is dropped.
A skipped directory, one that starts with an underscore or is named in ``skip_dir_names``, is reported to ``on_skipped_dir`` and not entered.
Any other parenthesised name is routed literally, and ``next.E169`` reports it.

The walk flattens a group into the level that holds it.
It collects the page of the directory, then the pages of its groups, which come in name order with a nested group depth-first, and then the routed children of the directory and of every group.
The children are sorted again by the route rank only when a group contributed, so a tree without groups pays no sort.
The walk yields the group-free URL trail, so ``pages/(app)/[id]/page.py`` and ``pages/new/page.py`` rank as siblings, and ``new/`` is tried before ``[id]/``.
Two pages on one URL, whether from a group and its parent or from two groups, are the ``next.E015`` duplicate, and its hint explains that a group leaves the URL when a source passes through one.
Alongside the URL trail the walk carries the raw file-system trail, which keeps the groups, and reports it to ``on_skipped_dir`` so a ``_components`` folder scopes to the subtree below the group that holds it.

An intercepting directory is named by a marker and a segment.
The scope of an intercept is the group-free URL trail of the directory that holds it.
``(.)x`` targets the scope plus ``x``, ``(..)x`` the scope minus its last segment plus ``x`` with one more segment dropped for each repeated ``(..)``, and ``(...)x`` targets ``x`` from the tree root.
A ``(..)`` that climbs past the root drops the intercept and ``next.E171`` reports it.
The subtree under the directory walks with its trail seeded to the target, so ``feed/(.)photo/[id]/page.py`` targets ``feed/photo/[id]``.
Groups inside the directory flatten, and an intercept inside an intercept is never read.
Each page found is handed to the ``on_intercept`` callback as an ``InterceptPage`` and never yielded as a route, so it gets no URL pattern, no URL name, no ``next.E015`` and no sitemap entry.

The file router publishes the intercepts after the walk of a page root, in ``next.intercept.intercepts``, an ``InterceptTable``.
A rule is published only when its target trail matches exactly a trail the same walk routed, so ``[id]`` does not match ``[int:id]``, and ``next.E170`` reports a rule without a target.
A rule whose directory lies under a nested page root belongs to the inner root and is left out here.
The router imports the page module of each published intercept, as it does for a routed page, so its context, metadata, actions, and access rules register.
``publish(root, rules, generation=)`` replaces every rule of that root in one assignment, and a root with no intercepts publishes an empty set, so a rule deleted on disk vanishes at the next reload.
``generation`` guards against a race with a reload.
The walk reads the table's ``generation`` before it starts, a reload bumps it, and a walk that began before the reload is refused at publish, so its stale rules never overwrite the cleared table.
``intercepts.of(target_file)`` returns the rules over one routed page in file order with a single dictionary lookup, and ``all()`` returns every rule.
A router reload clears the table through the ``forget_intercepts`` receiver of ``router_reloaded``, and the rebuild publishes it again.

Each published rule sends ``intercept_registered``, only when the publish was accepted.
The table is read at request time to pick the intercepting page that serves a zone request over its target.

``manage.py routes`` lists an intercepting page as an extra row right after the row of its target.
``manage.py permissions`` lists it with the kind ``intercept`` and the access rule of its own chain.
:doc:`/content/ref/management` describes both tables.

Trees placed by pages()
-----------------------

``urlconf_mounts()`` walks the include tree of ``ROOT_URLCONF`` once per root resolver, so :func:`~django.urls.clear_url_caches` or a swapped URLconf walks it again.
It records the route ``next.urls`` is included under and every ``pages()`` include with its route joined through the enclosing includes.
Both include targets are ``MountedPatterns`` sequences, and the walk notes them without iterating, because iterating one builds page routes and the build itself reads the walk.

The trees the walk finds through ``pages()`` drop out of the auto-assembly of ``next.urls``, and ``page_roots`` reports them beside the auto-assembled ones with their ``prefix`` set, so component registration, static discovery, and the checks see every tree the router serves.

A ``pages()`` tree builds tree-relative patterns through the first ``FileRouterBackend``, wraps them in the ``URL_RESOLVER`` class, and rebuilds them when the router version moves, while Django joins in the outer route.
Every page view the file router builds, of ``next.urls`` and of a ``pages()`` tree alike, carries ``next_route_trail``, its route relative to its tree.
The breadcrumb resolution reads it to pass on the kwargs the includes around the tree capture to every crumb, see :doc:`/content/topics/seo/breadcrumbs`.
Layouts, contexts, access declarations, and breadcrumbs never cross that boundary.
The views also carry ``next_page_path`` and the ``login_required`` attribute ``LoginRequiredMiddleware`` reads, ``False`` for a page whose rule is public through its own ``page.public()`` or a ``page.public(inherit=True)`` above it, and the ``page.py`` chain of a page ends at the root of its own tree.

Reload mechanics
----------------

``router_manager.reload()`` does five things in order.

1. Bumps the version counter that invalidates the cached pattern concat and the resolver's route index.
2. Rebuilds the backend list from ``PAGE_BACKENDS``, sending ``router_backend_loaded`` once per entry the shared loader instantiates.
3. Publishes the new list in one assignment, so a read racing the build never sees a partial one.
4. Clears the Django URL resolver cache.
5. Emits the ``router_reloaded`` signal.

``notify=False`` suppresses three of those effects, the per-entry ``router_backend_loaded``, the URL-cache clear, and ``router_reloaded``.
The version bump and the rebuild itself always happen, which is what lets a receiver of ``router_reloaded`` reload the backends without re-entering its own signal.
The next request observes the new patterns without a process restart.
The ``router_reloaded`` receivers also drop the intercept table, which the rebuild fills again.
Long lived processes such as websocket subscribers listen for the signal to refresh cached URL references.

The manager also reloads on two signals of its own accord.
A ``settings_reloaded`` receiver rebuilds the routers and drops the built URL resolver, so a new ``URL_RESOLVER`` or a new ``PAGE_BACKENDS`` list takes effect without a restart.
A :data:`~django.test.signals.setting_changed` receiver rebuilds them whenever ``INSTALLED_APPS`` or ``ROOT_URLCONF`` moves, because a file router memoises the pattern list of each application tree and reads its ``pages()`` includes from the root URLconf, so it would otherwise serve the routes of the previous value.
That second receiver is what makes :func:`~django.test.override_settings` over ``INSTALLED_APPS`` or ``ROOT_URLCONF`` produce the routes of the swapped value.
Both receivers call the notifying form, so each of them fires ``router_backend_loaded`` and ``router_reloaded`` as an explicit ``reload()`` does.

Multiple backends
-----------------

The settings list accepts more than one backend.
Each backend reports its own list of patterns.
Resolution preserves the concatenated list order, so the first match wins.

If two routes convert to exactly the same Django path string the ``check_url_patterns`` system check reports the conflict at startup as ``next.E015``, whether they come from one tree or several.
The comparison is string equality after bracket conversion, so semantic overlap between typed converters, such as ``posts/[int:id]`` next to ``posts/[id]``, is not reported.
The comparison keys each route from the URLconf root, the prefixes of ``pages()`` includes too, so two trees placed onto one URL conflict as well.
The same collection pass owns the duplicate parameter report, failing with ``next.E028`` and listing every conflicting name in one message, or with ``next.E152`` when the repeated name comes from the ``pages()`` prefix, and reports a router whose collection raises as ``next.E016``.
``check_reverse_name_collisions`` reuses the collection but drops its errors, so ``next.E016`` and ``next.E028`` never appear twice.

Route introspection
-------------------

Beside the abstract ``generate_urls``, ``RouterBackend`` carries three concrete introspection methods, ``page_roots``, ``components_folder_name``, and ``skip_dir_names``.
They are a public contract, the only thing the system checks and the development watcher read from a backend, and :doc:`/content/howto/write-a-router-backend` is the canonical description of each method and of the safe defaults that make overriding them the opt-in.

The checks walk the reported trees themselves, refusing every directory whose name starts with an underscore, the names ``skip_dir_names`` returns, and the folder ``components_folder_name`` names, while the watcher derives its watch specs from ``page_roots`` and ``components_folder_name`` alone, as :doc:`autoreload` describes.

``RouterManager.backends`` is the public read of the loaded backend list, a tuple in routing order.
The first read loads the backends from ``PAGE_BACKENDS``, the same build the first iteration of the manager runs, so a caller that asks before the first resolve reads the configured set rather than an empty tuple.
Later reads hand back a snapshot of the loaded list, and ``reload()`` rebuilds it.
The ``repr`` of the manager reports the raw state instead of loading, so inspecting it under a debugger neither rebuilds the routes nor fires ``router_reloaded``.

Extension points
----------------

- Subclass ``RouterBackend`` to feed the resolver from a different source, or subclass ``FileRouterBackend`` to add patterns or augment naming on the file-based backend.
- Reference a custom backend through its dotted path under ``PAGE_BACKENDS``, and take the entry as the single constructor argument.
- Name a resolver class in ``NEXT_FRAMEWORK["URL_RESOLVER"]`` to replace the trie dispatch.
  The value is a dotted path to a Django ``URLResolver`` subclass, which the manager instantiates with an empty route pattern and the lazy pattern sequence.
  ``"django.urls.resolvers.URLResolver"`` restores the plain linear scan, and a subclass of ``TrieURLResolver`` keeps the index while changing the candidate order.
- Subscribe to ``route_registered`` to observe each new pattern.
  It fires once per discovered pattern with ``sender=FileRouterBackend`` and the ``url_path`` and ``file_path`` keyword arguments, where ``url_path`` is the trail of the page relative to its tree.
- Subscribe to ``router_backend_loaded`` to observe each backend the manager builds.
  It fires once per instantiated ``PAGE_BACKENDS`` entry with the resolved router class as sender and the ``config`` copy and the built ``instance`` as keyword arguments, so a receiver connected with ``sender=`` hears one backend family alone.
  ``reload(notify=False)`` builds the backends without sending it.
- Subscribe to ``router_reloaded`` to observe the rebuild itself, sent by the ``RouterManager`` class with no keyword arguments.

- Subscribe to ``intercept_registered`` to observe each intercept rule the file router publishes.
  It fires once per rule with ``sender=FileRouterBackend`` and the ``target_file``, ``file_path``, and ``marker`` keyword arguments, and only after the table accepted the publish.

The four signals above are the whole signal surface of the URL area.
See :doc:`/content/ref/signals` for their payloads and :doc:`/content/topics/signals` for the wider catalog.

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the topic guide.
   :doc:`/content/topics/url-reversing` for the reverse helpers.
   :doc:`autoreload` for the reload mechanics tied to the watcher.
