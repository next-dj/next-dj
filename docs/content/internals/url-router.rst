.. _internals-url-router:

URL router
==========

This page covers how the file router scans the filesystem, builds URL patterns, and reloads at runtime.

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
   ``FileRouterBackend`` implements file based routing.
   ``RouterFactory`` looks up backends by dotted path.

``next.urls.parser``.
   Turns directory names into URL patterns.
   Recognises ``[name]``, ``[type:name]``, and ``[[name]]`` shapes.
   The ``default_url_parser.parse_url_pattern`` method is the single source of bracket conversion, used by both the router and the system checks, so a route and its check always agree on the resulting Django path string.

``next.urls.manager``.
   ``RouterManager`` builds the active pattern list, exposes ``reload``, and emits the ``router_reloaded`` signal.
   The module-level ``urlpatterns`` is a list with one ``TrieURLResolver`` wrapping the lazy router and form-action pattern sequence.

``next.urls.resolver``.
   ``TrieURLResolver`` narrows each ``resolve()`` call to a few candidates through a static route map and a segment trie, with the inherited linear scan as fallback.

``next.urls.dispatcher``.
   ``FilesystemTreeDispatcher`` walks the pages directory tree and yields ``(url_path, page_file)`` pairs that the router turns into URL patterns.
   The module-level helper ``scan_pages_tree`` instantiates the dispatcher with the configured skip set and returns the same pairs as an iterator.

``next.urls.markers``.
   Hosts the ``DUrl`` and ``DQuery`` annotation markers, the four request/URL/query parameter providers, and the ``get_multi_values`` helper.
   See :doc:`/content/topics/dependency-injection` for the marker semantics and the provider order.

``next.urls.reverse``.
   ``page_reverse``, ``page_reverse_lazy``, and ``with_query`` helpers.

URL name computation
--------------------

Names follow ``next:page_<segments>`` where the segments come from the directory path.

- Static segments contribute their directory name unchanged.
- Captured segments contribute the raw bracket text with separators collapsed to underscores, so a converter prefix such as ``int:`` stays in the name.
- Wildcard segments contribute the parameter name without brackets.

The template ``URL_NAME_TEMPLATE`` controls the format.
The default ``page_{name}`` produces the names listed in :doc:`/content/topics/file-router`.

The name computation collapses ``/``, brackets, ``:``, ``-``, and ``_`` into a single underscore, so distinct routes such as ``foo-bar`` and ``foo_bar`` can produce the same name.
The ``check_reverse_name_collisions`` system check walks every page tree of every router, computes the reverse name of each route through the same parser, and fails with ``next.E039`` when two distinct routes collapse to one name, listing the conflicting paths.

Resolution algorithm
--------------------

``include("next.urls")`` mounts a single ``TrieURLResolver`` that wraps the lazy router and form-action pattern sequence.
The sequence caches the concatenated pattern list against a pair of version counters, one bumped by ``router_manager.reload()`` and one bumped when form actions register or clear through the form-action manager.
The counters are read after the pattern build, because expanding page modules can register form actions mid-build, so the cache stays valid for the post-registration state.
A registration that writes into a backend directly, bypassing the manager, is not tracked.

The resolver builds a route index from the same sequence and versions it with the same counter pair.

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

Reload mechanics
----------------

``router_manager.reload()`` does four things in order.

1. Bumps the version counter that invalidates the cached pattern concat and the resolver's route index.
2. Rebuilds the backend list from ``PAGE_BACKENDS``.
3. Clears the Django URL resolver cache.
4. Emits the ``router_reloaded`` signal.

The next request observes the new patterns without a process restart.
Long lived processes such as websocket subscribers listen for the signal to refresh cached URL references.

Multiple backends
-----------------

The settings list accepts more than one backend.
Each backend reports its own list of patterns.
Resolution preserves the concatenated list order, so the first match wins.

If two routes convert to exactly the same Django path string the ``check_url_patterns`` system check reports the conflict at startup as ``next.E015``, whether they come from one tree or several.
The comparison is string equality after bracket conversion, so semantic overlap between typed converters, such as ``posts/[int:id]`` next to ``posts/[id]``, is not reported.
The same collection pass owns the duplicate parameter report, failing with ``next.E028`` and listing every conflicting name in one message, and reports a router whose collection raises as ``next.E016``.
``check_reverse_name_collisions`` reuses the collection but drops its errors, so ``next.E016`` and ``next.E028`` never appear twice.

Extension points
----------------

- Subclass ``RouterBackend`` to feed the resolver from a different source, or subclass ``FileRouterBackend`` to add patterns or augment naming on the file-based backend.
- Register a custom backend in ``RouterFactory`` and reference it through the settings dotted path.
- Subscribe to ``route_registered`` to observe each new pattern.
  It fires once per discovered pattern with ``sender=FileRouterBackend`` and the ``url_path`` and ``file_path`` keyword arguments.
  See :doc:`/content/ref/signals`.

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the topic guide.
   :doc:`/content/topics/url-reversing` for the reverse helpers.
   :doc:`autoreload` for the reload mechanics tied to the watcher.
