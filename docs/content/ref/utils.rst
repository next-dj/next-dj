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
It lives here rather than beside the router because the system checks build one too, and it is re-exported as ``next.urls.PageRoot`` for the router contract that produces it.

The rest of the module is framework machinery and is excluded from the listing below.
It holds ``walk_page_tree``, the depth-first page-tree walk the file router and the system checks both run, ``page_roots_shape_error``, the shared shape probe a check runs over what a router reports, ``template_edits_watched``, the ``DEBUG`` predicate the page, component, and static caches read before they stat anything, and ``normalise_route_name``, the one reading of a hyphen as an underscore that the URL parser and the directory check share.
Those four sit beside ``PageRoot`` for the reason ``PageRoot`` sits here, that more than one subsystem reads each of them.
``WEB_SCHEMES``, ``is_int``, ``ROUTE_BRACKET_PATTERN``, and ``is_dynamic_trail`` join them for the same reason, the URL schemes a head tag or a sitemap may name, the strict int probe the metadata normaliser and the SEO checks share, and the bracket-segment pattern behind the predicate that tells a route with a parameter from a static one.
``TreeSource`` and ``load_tree_source`` are the one loader of a Python file at the top of a page tree, which ``sitemap.py``, ``robots.py``, and ``scripts.py`` all go through.
``load_tree_source`` executes the file as its own module, keeps any failure of that user code on the returned ``TreeSource`` for the checks to report, and records the file's modification time, so ``TreeSource.stale()`` tells a watched process to read it again and an edit shows without a restart.
``UNSET``, the single member of the ``Unset`` enum that ``next.conf.sentinels`` defines and ``next.utils`` re-exports, is the one sentinel for an absent value, told apart from a ``None`` a memo may hold.
``decode_url_path`` and ``exec_module_file`` are the smaller shared helpers behind them.

Five more flat modules sit at the root of the package for the same reason, and all five are framework-internal.
``next.caches`` holds ``BoundedCache``, ``LruCache``, and ``PageCache``, the bounded caches every path-keyed memo of the framework is built from, each owning its bound and the policy it gives an entry up by.
A ``PageCache`` keys a memo by page, and each URL build sets its bound to two entries per routed page, never below 2048 entries, so a large site does not evict every entry before its next read and a smaller build releases the entries of the pages it no longer mounts.
``next.introspect`` backs decorator registration, attributing a decorated object to the file where it was declared, naming it for diagnostics, and collecting the registrations bound to another file.
``next.discovery`` holds the per-run router manager and the walk of the page trees it routes, which the system checks, the component sources, and the page scan all read, and it reaches the routers through ``next.ports`` rather than by importing ``next.urls``.
``next.diagnostics`` holds ``FailureLog``, the containment every area routes a failure of user or third-party code through, and ``BackendReadLog``, the guarded read a watch layer puts a third-party backend answer through, which drops a raising or malformed answer.
``FailureLog.contain`` re-raises an exception of its ``pass_through`` tuple in every mode.
The default, ``INTENDED_EXCEPTIONS``, holds ``Http404``, ``PermissionDenied``, ``SuspiciousOperation``, and ``BadRequest``, so Django answers each with its own response.
Django converts these only when a view raises them, so a call site outside any view, such as URL resolution or a middleware, passes ``pass_through=()`` and contains them like any other failure.
Under ``DEBUG`` or ``STRICT_LOADING`` it re-raises any other exception with a note naming the source and the fix.
Otherwise it marks the render degraded, so the response is kept out of shared caches, and logs the failure once per key.
A key that keeps failing is logged again once ``QUIET_PERIOD``, 600 seconds, has passed since its last record, and that record carries the number of occurrences left out in its ``suppressed`` attribute.
A reload of the framework settings, or ``next.testing.reset_failure_logs()``, makes every failure log again.
The keys a log holds are bounded, and a key names a source such as a file or a setting, never request data.
``next.seeding`` holds the render-context keys the areas share, the ``RenderFrame`` that seeds them for a caller building its context from scratch, and ``seed_collector``, the one hydration of a ``StaticCollector`` that a full page render and a standalone zone render both run through the ``next.ports`` static slot.

Public API
----------

.. automodule:: next.utils
   :members:
   :exclude-members: walk_page_tree, page_roots_shape_error, template_edits_watched, normalise_route_name, stat_mtime_ns, resolved_tree, forget_resolved_trees, on_forget_resolved_trees, MAX_ANCESTOR_WALK_DEPTH, WEB_SCHEMES, is_int, ROUTE_BRACKET_PATTERN, is_dynamic_trail, TreeSource, load_tree_source, UNSET, Unset, decode_url_path, exec_module_file

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` documents the ``DIRS`` semantics that ``classify_dirs_entries`` supports.
