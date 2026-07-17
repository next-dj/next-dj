.. _internals-url-router:

URL Router
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
       Manager --> Resolver[Django URL resolver]
       Resolver --> PageView[Page view]
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

``next.urls.dispatcher``.
   ``FilesystemTreeDispatcher`` walks the pages directory tree and yields ``(url_path, page_file)`` pairs that the router turns into URL patterns.
   The module-level helper ``scan_pages_tree`` instantiates the dispatcher with the configured skip set and returns the same pairs as an iterator.

``next.urls.markers``.
   Hosts the ``DUrl`` and ``DQuery`` annotation markers, the four request/URL/query parameter providers, and the ``get_multi_values`` helper.
   See :doc:`/content/topics/dependency-injection` for the marker semantics and the provider order.

``next.urls.reverse``.
   ``page_reverse``, ``page_reverse_lazy``, and ``with_query`` helpers.

URL Name Computation
--------------------

Names follow ``next:page_<segments>`` where the segments come from the directory path.

- Static segments contribute their directory name unchanged.
- Captured segments contribute the raw bracket text with separators collapsed to underscores, so a converter prefix such as ``int:`` stays in the name.
- Wildcard segments contribute the parameter name without brackets.

The template ``URL_NAME_TEMPLATE`` controls the format.
The default ``page_{name}`` produces the names listed in :doc:`/content/topics/file-router`.

The name computation collapses ``/``, brackets, ``:``, ``-``, and ``_`` into a single underscore, so distinct routes such as ``foo-bar`` and ``foo_bar`` can produce the same name.
The ``check_reverse_name_collisions`` system check walks every page tree of every router, computes the reverse name of each route through the same parser, and fails with ``next.E039`` when two distinct routes collapse to one name, listing the conflicting paths.

Reload Mechanics
----------------

``router_manager.reload()`` does three things in order.

1. Rebuilds the backend list from ``PAGE_BACKENDS``.
2. Clears the Django URL resolver cache.
3. Emits the ``router_reloaded`` signal.

The next request observes the new patterns without a process restart.
Long lived processes such as websocket subscribers listen for the signal to refresh cached URL references.

Multiple Backends
-----------------

The settings list accepts more than one backend.
Each backend reports its own list of patterns.
The Django URL resolver checks them in order and the first match wins.

If two routes convert to exactly the same Django path string the ``check_url_patterns`` system check reports the conflict at startup as ``next.E015``, whether they come from one tree or several.
The comparison is string equality after bracket conversion, so semantic overlap between typed converters, such as ``posts/[int:id]`` next to ``posts/[id]``, is not reported.
The same collection pass owns the duplicate parameter report, failing with ``next.E028`` and listing every conflicting name in one message, and reports a router whose collection raises as ``next.E016``.
``check_reverse_name_collisions`` reuses the collection but drops its errors, so ``next.E016`` and ``next.E028`` never appear twice.

Extension Points
----------------

- Subclass ``RouterBackend`` to feed the resolver from a different source, or subclass ``FileRouterBackend`` to add patterns or augment naming on the file-based backend.
- Register a custom backend in ``RouterFactory`` and reference it through the settings dotted path.
- Subscribe to ``route_registered`` to observe each new pattern.
  It fires once per discovered pattern with ``sender=FileRouterBackend`` and the ``url_path`` and ``file_path`` keyword arguments.
  See :doc:`/content/ref/signals`.

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the topic guide.
   :doc:`/content/topics/url-reversing` for the reverse helpers.
   :doc:`autoreload` for the reload mechanics tied to the watcher.
