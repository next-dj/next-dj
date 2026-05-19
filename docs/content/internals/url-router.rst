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

``next.urls.manager``.
   ``RouterManager`` builds the active pattern list, exposes ``reload``, and emits the ``router_reloaded`` signal.

``next.urls.dispatcher``.
   ``FilesystemTreeDispatcher`` walks the pages directory tree and yields ``(url_path, page_file)`` pairs that the router turns into URL patterns.

``next.urls.markers``.
   ``DUrl`` and ``DQuery`` markers used in annotations.
   ``DUrl.__class_getitem__`` builds the marker for the bare-type, named-key, and named-key-with-type forms, wrapping string and tuple arguments in a ``GenericAlias`` the providers introspect.
   The four parameter providers ``HttpRequestProvider``, ``UrlByAnnotationProvider``, ``UrlKwargsProvider``, and ``QueryParamProvider`` that register with the resolver.
   The ``get_multi_values`` helper that reads a multi-value query parameter outside the resolver.

``next.urls.reverse``.
   ``page_reverse`` and ``with_query`` helpers.

URL Name Computation
--------------------

Names follow ``next:page_<segments>`` where the segments come from the directory path.

- Static segments contribute their directory name unchanged.
- Captured segments contribute their parameter name without the type prefix.
- Wildcard segments contribute the parameter name without brackets.

The template ``URL_NAME_TEMPLATE`` controls the format.
The default ``page_{name}`` produces the names listed in :doc:`/content/topics/file-router`.

Reload Mechanics
----------------

``router_manager.reload()`` does three things in order.

1. Rebuilds the backend list from ``DEFAULT_PAGE_BACKENDS``.
2. Clears the Django URL resolver cache.
3. Emits the ``router_reloaded`` signal.

The next request observes the new patterns without a process restart.
Long lived processes such as websocket subscribers listen for the signal to refresh cached URL references.

Multiple Backends
-----------------

The settings list accepts more than one backend.
Each backend reports its own list of patterns.
The Django URL resolver checks them in order and the first match wins.

If two routes resolve to the same Django path the ``next.E015`` system check reports the conflict at startup, whether they come from one tree or several.

Extension Points
----------------

- Subclass ``FileRouterBackend`` to add patterns or augment naming.
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
