.. _ref-utils:

Utils reference
===============

Module summary
--------------

``next.utils`` exposes two helpers that project code can import, ``resolve_base_dir`` and ``classify_dirs_entries``, and the ``PageRoot`` value object.
The rest of the module is framework machinery and is excluded from the listing below.
It backs decorator registration, attributing a decorated object to the file where it was declared, naming it for diagnostics, and collecting the registrations that landed on another file.
It also holds ``walk_page_tree``, the depth-first page-tree walk the file router and the system checks both run, and ``page_roots_shape_error``, the shared shape probe a check runs over what a router reports.
Both live here for the same reason ``PageRoot`` does.

``resolve_base_dir`` returns ``settings.BASE_DIR`` coerced to ``pathlib.Path``, or ``None`` when it is unset, for backends that resolve project-relative paths.
``classify_dirs_entries`` splits a backend ``DIRS`` list into existing directory roots and plain skip-name segments, the same split the file router applies.
``PageRoot`` pairs a page tree with the label a report names it by.
It lives here rather than beside the router because the system checks build one too, and it is re-exported as ``next.urls.PageRoot`` for the router contract that produces it.

Public API
----------

.. automodule:: next.utils
   :members:
   :exclude-members: _classify_one_dir_entry, callable_name, defining_file, walk_page_tree, page_roots_shape_error, MisattributedContext, MisattributionLog

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` documents the ``DIRS`` semantics that ``classify_dirs_entries`` supports.
