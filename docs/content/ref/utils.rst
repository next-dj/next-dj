.. _ref-utils:

Utils reference
===============

Module summary
--------------

``next.utils`` exposes two helpers that project code can import, ``resolve_base_dir`` and ``classify_dirs_entries``.
The rest of the module backs decorator registration.
It attributes a decorated object to the file where it was declared, names it for diagnostics, and collects the registrations that landed on another file.
Those helpers are excluded from the listing below.

``resolve_base_dir`` returns ``settings.BASE_DIR`` coerced to ``pathlib.Path``, or ``None`` when it is unset, for backends that resolve project-relative paths.
``classify_dirs_entries`` splits a backend ``DIRS`` list into existing directory roots and plain skip-name segments, the same split the file router applies.

Public API
----------

.. automodule:: next.utils
   :members:
   :exclude-members: _classify_one_dir_entry, callable_name, defining_file, MisattributedContext, MisattributionLog

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` documents the ``DIRS`` semantics that ``classify_dirs_entries`` supports.
