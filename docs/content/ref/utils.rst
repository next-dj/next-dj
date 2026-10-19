.. _ref-utils:

Utils reference
===============

Module summary
--------------

``next.utils`` exposes two helpers that project code can import, ``resolve_base_dir`` and ``classify_dirs_entries``, and the ``PageRoot`` value object.
``resolve_base_dir`` returns ``settings.BASE_DIR`` coerced to ``pathlib.Path``, or ``None`` when it is unset, for backends that resolve project-relative paths.
``classify_dirs_entries`` splits a backend ``DIRS`` list into existing directory roots and plain skip-name segments, the same split the file router applies.
A ``DIRS`` that is no sequence of trees raises :class:`~next.errors.InvalidDirsError`, a bare string included, because iterating a string would split it into characters.
``PageRoot`` pairs a page tree with the label a report names it by.
``prefix`` is the route a ``pages()`` include places the tree under, in Django :func:`~django.urls.path` syntax, and ``None`` for a tree ``next.urls`` routes, and ``namespace`` is the URL namespace of its names, ``next`` unless a ``pages()`` include names another.
Both fields default, so a custom backend that builds ``PageRoot(path=..., label=...)`` reports a tree of ``next.urls``.
``route_trail(trail)`` returns the trail a page at the tree-relative ``trail`` routes by, prefix included.
It lives here rather than beside the router because the system checks build one too, and it is re-exported as ``next.urls.PageRoot`` for the router contract that produces it.

The rest of the module is framework machinery and is excluded from the listing below.
It holds ``walk_page_tree``, the depth-first page-tree walk the file router and the system checks both run, ``page_roots_shape_error``, the shared shape probe a check runs over what a router reports, ``template_edits_watched``, the ``DEBUG`` predicate the page, component, and static caches read before they stat anything, and ``normalise_route_name``, the one reading of a hyphen as an underscore that the URL parser and the directory check share.
Those four sit beside ``PageRoot`` for the reason ``PageRoot`` sits here, that more than one subsystem reads each of them.
``WEB_SCHEMES``, ``is_int``, ``ROUTE_BRACKET_PATTERN``, and ``is_dynamic_trail`` join them for the same reason, the URL schemes a head tag or a sitemap may name, the strict int probe the metadata normaliser and the SEO checks share, and the bracket-segment pattern behind the predicate that tells a route with a parameter from a static one.
``prefix_trail`` and ``join_trail`` sit beside that pattern, the first reading a ``pages()`` prefix as a trail with ``<int:wallet>`` turned into ``[int:wallet]``, the second joining two trails with one slash, and the router, the URL checks, the breadcrumb resolution, and ``PageRoot.route_trail`` all build the full trail of a page under a prefix through them.
``TreeSource`` and ``load_tree_source`` are the one loader of a Python file at the top of a page tree, which ``sitemap.py``, ``robots.py``, and ``scripts.py`` all go through.
``load_tree_source`` executes the file as its own module, keeps any failure of that user code on the returned ``TreeSource`` for the checks to report, and records the file's modification time, so ``TreeSource.stale()`` tells a watched process to read it again and an edit shows without a restart.
``decode_url_path`` and ``exec_module_file`` are the smaller shared helpers behind them, and ``is_middleware`` and ``middleware_listed`` tell whether a ``MIDDLEWARE`` entry names a class or a subclass of it, for the checks that read the middleware order.

Five more flat modules sit at the root of the package for the same reason, and all five are framework-internal.
``next.caches`` holds ``BoundedCache``, ``LruCache``, and ``PageCache``, the bounded caches every path-keyed memo of the framework is built from, each owning its bound and the policy it gives an entry up by.
A ``PageCache`` keys a memo by page, and each URL build sets its bound to two entries per routed page, never below 2048 entries, so a large site does not evict every entry before its next read and a smaller build releases the entries of the pages it no longer routes.
``next.introspect`` backs decorator registration, attributing a decorated object to the file where it was declared, naming it for diagnostics, and collecting the registrations bound to another file.
``next.discovery`` holds the per-run router manager and the walk of the page trees it routes, which the system checks, the component sources, and the page scan all read, and it reaches the routers through ``next.ports`` rather than by importing ``next.urls``.
``next.diagnostics`` holds ``FailureLog``, the containment every area routes a failure of user or third-party code through, and ``BackendReadLog``, the guarded read a watch layer puts a third-party backend answer through, which drops a raising or malformed answer.
``FailureLog.guard(key, message, *args, catch=(Exception,), pass_through=INTENDED_EXCEPTIONS, detail=None)`` answers a ``Guard``, the context manager that applies the policy :ref:`topics-failure-containment` states to the exception its block raises.
After the block, ``failed`` tells whether it contained one, and ``error`` holds the exception for a caller that memoises the failure.
``catch`` narrows the exceptions it contains, and only an ``Exception`` qualifies, so ``KeyboardInterrupt`` and ``SystemExit`` always propagate.
The default ``pass_through``, ``INTENDED_EXCEPTIONS``, lets a view's ``Http404``, ``PermissionDenied``, ``SuspiciousOperation``, and ``BadRequest`` propagate, and a call site outside any view passes ``pass_through=()``.
``detail`` draws message arguments from the exception, ``type_name`` and ``exception_text`` naming its type or text, while the key stays the one the caller passed, so request data never reaches it.
``Capture``, the base of ``Guard``, holds an exception of its ``catch`` in ``error`` and suppresses it without logging or degrading, for a caller that handles the failure itself.
``next.seeding`` holds the render-context keys the areas share, the ``RenderFrame`` that seeds them for a caller building its context from scratch, and ``seed_collector``, the one hydration of a ``StaticCollector`` that a full page render and a standalone zone render both run through the ``next.ports`` static slot.

Public API
----------

.. automodule:: next.utils
   :members:
   :exclude-members: walk_page_tree, page_roots_shape_error, template_edits_watched, normalise_route_name, stat_mtime_ns, resolved_tree, forget_resolved_trees, on_forget_resolved_trees, MAX_ANCESTOR_WALK_DEPTH, WEB_SCHEMES, is_int, ROUTE_BRACKET_PATTERN, is_dynamic_trail, prefix_trail, join_trail, TreeSource, load_tree_source, decode_url_path, exec_module_file, is_middleware, middleware_listed

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` documents the ``DIRS`` semantics that ``classify_dirs_entries`` supports.
