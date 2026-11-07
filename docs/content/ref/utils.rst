.. _ref-utils:

Utils reference
===============

Module summary
--------------

``next.utils`` exposes ``resolve_base_dir``, ``classify_dirs_entries``, ``redirect_or_fallback``, the ``PageRoot`` value object, and the readers of the two directory-name grammars that route groups and intercepting routes use.
``resolve_base_dir`` returns ``settings.BASE_DIR`` coerced to ``pathlib.Path``, or ``None`` when it is unset, for backends that resolve project-relative paths.
``classify_dirs_entries`` splits a backend ``DIRS`` list into existing directory roots and plain skip-name segments, the same split the file router applies.
A ``DIRS`` that is no sequence of trees raises :class:`~next.errors.InvalidDirsError`, a bare string included, because iterating a string would split it into characters.
``PageRoot`` pairs a page tree with the label a report names it by and the key its co-located static files are named by.
``prefix`` is the route a ``pages()`` include places the tree under, in Django :func:`~django.urls.path` syntax, and ``None`` for a tree ``next.urls`` routes, and ``namespace`` is the URL namespace of its names, ``next`` unless a ``pages()`` include names another.
``route_trail(trail)`` returns the trail a page at the tree-relative ``trail`` routes by, prefix included.
It lives here rather than beside the router because the system checks build one too, and it is re-exported as ``next.urls.PageRoot`` for the router contract that produces it.

A directory named ``(name)`` is a route group and adds nothing to the URL.
``ROUTE_GROUP_PATTERN`` is its grammar, a letter or ``_`` first and then letters, digits, ``_``, and ``-`` inside the parentheses.
``is_route_group(name)`` tells whether a directory name is a group, and ``strip_route_groups(raw_trail)`` returns a file-system trail with its group segments left out.
A directory named with a marker and a segment, such as ``(.)photo``, is an intercepting route.
``INTERCEPT_PATTERN`` is its grammar, one marker kind and then the segment it targets, where ``(.)`` names a sibling, ``(..)`` repeated once per level goes up, and ``(...)`` starts at the tree root.
``parse_intercept(name)`` returns the marker and the targeted segment of a directory name, or ``None`` when the name is no intercept.
``resolve_intercept(scope_trail, marker, rest)`` returns the URL trail the intercept targets from the group-free URL trail of the directory that holds it, or ``None`` when ``(..)`` climbs past the tree root.
``InterceptPage`` is the frozen value the walk reports for a page under an intercept directory, with the ``target_trail`` it sits over, its ``file``, the marked ``source`` directory, and the ``marker``.

``redirect_or_fallback(target, fallback, *, status=302)`` redirects to ``target``, or to ``fallback`` when Django refuses it as a ``Location``.

The rest of the module is shared framework machinery and is excluded from the listing below.
It holds the page-tree walk described next, the listing and shape probes the router and the system checks share, the trail helpers behind ``PageRoot.route_trail``, and ``TreeSource`` with ``load_tree_source``, the one loader of a Python file at the top of a page tree.
``load_tree_source`` keeps any failure of that user code on the returned ``TreeSource`` for the checks to report, and ``TreeSource.stale()`` tells a watched process to read the file again.

Other flat modules at the root of the package, among them ``next.caches``, ``next.introspect``, ``next.discovery``, ``next.diagnostics``, ``next.stamps``, and ``next.seeding``, are framework-internal.
:doc:`/content/internals/overview` maps each one, and :ref:`topics-failure-containment` covers ``FailureLog`` and its ``guard`` boundary.
``next.seeding`` holds the ``RenderFrame`` that seeds a render context, ``ambient_frame(frame)`` publishes one for the widgets a binder had no instance to reach, and ``current_ambient_frame()`` reads it.

The page-tree walk
------------------

``walk_page_tree(tree_root, skip_dir_names=(), *, on_skipped_dir=None, on_intercept=None)`` yields ``(url_trail, page_file)`` pairs.
The ``url_trail`` is the group-free URL trail of the page relative to ``tree_root``, so ``pages/(auth)/login/page.py`` yields ``login``.
A group joins the level that holds it, and the children of that level are ranked together, see :doc:`/content/internals/url-router`.
``on_skipped_dir(child, tree_root, scope_trail)`` runs for each directory the walk refuses, such as a ``_components`` folder.
Its ``scope_trail`` is the raw file-system trail of the holding directory with the groups kept, which is what scopes a components folder to the subtree below the group that holds it.
The raw trail of a yielded page is ``page_file.parent.relative_to(tree_root)``.
``on_intercept(intercept_page)`` receives an ``InterceptPage`` for each page under an intercept directory, and the walk never yields such a page as a route.
A group inside an intercept directory flattens, and an intercept inside an intercept is not read.

Public API
----------

.. automodule:: next.utils
   :members:
   :exclude-members: walk_page_tree, PageDir, list_page_dir, routes_into, PAGE_FILES, page_roots_shape_error, template_edits_watched, normalise_route_name, app_holding, forget_app_holdings, resolved_tree, forget_resolved_trees, on_forget_resolved_trees, MAX_ANCESTOR_WALK_DEPTH, WEB_SCHEMES, is_int, ROUTE_BRACKET_PATTERN, is_dynamic_trail, prefix_trail, join_trail, TreeSource, load_tree_source, decode_url_path, exec_module_file, is_middleware, middleware_listed, int_between, positive_number, non_negative_number, after_fork, is_within, child_nodelists, is_intercept_name, regex_trail, UNMOUNTED, LAYOUT_TEMPLATE

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` documents the ``DIRS`` semantics that ``classify_dirs_entries`` supports.
