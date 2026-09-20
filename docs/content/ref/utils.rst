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

Five more flat modules sit at the root of the package for the same reason, and all five are framework-internal.
``next.caches`` holds ``BoundedCache`` and ``LruCache``, the two bounded caches every path-keyed memo of the framework is built from, each owning its bound and the policy it gives an entry up by.
``next.introspect`` backs decorator registration, attributing a decorated object to the file where it was declared, naming it for diagnostics, and collecting the registrations that landed on another file.
``next.discovery`` holds the per-run router manager and the walk of the page trees it routes, which the system checks, the component sources, and the page scan all read, and it reaches the routers through ``next.ports`` rather than by importing ``next.urls``.
``next.diagnostics`` holds ``BackendReadLog``, the guarded read a watch layer puts a third-party backend answer through, which drops a raising or malformed answer and reports each failing source once per configuration.
``next.seeding`` holds the render-context keys the areas share, the ``RenderFrame`` that seeds them for a caller building its context from scratch, and ``seed_collector``, the one hydration of a ``StaticCollector`` that a full page render and a standalone zone render both run through the ``next.ports`` static slot.

Public API
----------

.. automodule:: next.utils
   :members:
   :exclude-members: walk_page_tree, page_roots_shape_error, template_edits_watched, normalise_route_name, stat_mtime_ns, resolved_tree, forget_resolved_trees, on_forget_resolved_trees, MAX_ANCESTOR_WALK_DEPTH

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` documents the ``DIRS`` semantics that ``classify_dirs_entries`` supports.
