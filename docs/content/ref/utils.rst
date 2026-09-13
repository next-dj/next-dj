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
It backs decorator registration, attributing a decorated object to the file where it was declared, naming it for diagnostics, and collecting the registrations that landed on another file.
It also holds ``walk_page_tree``, the depth-first page-tree walk the file router and the system checks both run, ``page_roots_shape_error``, the shared shape probe a check runs over what a router reports, and ``template_edits_watched``, the ``DEBUG`` predicate the page, component, and static caches read before they stat anything.
Those three sit beside ``PageRoot`` for the reason ``PageRoot`` sits here, that more than one subsystem reads each of them.

Public API
----------

.. automodule:: next.utils
   :members:
   :exclude-members: callable_name, code_filename, describe_callable, defining_file, walk_page_tree, page_roots_shape_error, template_edits_watched, store_bounded, store_capped, touch_bounded, stat_mtime_ns, resolved_tree, forget_resolved_trees, on_forget_resolved_trees, MAX_ANCESTOR_WALK_DEPTH, MisattributedContext, MisattributionLog

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` documents the ``DIRS`` semantics that ``classify_dirs_entries`` supports.
