.. _ref-utils:

Utils Reference
===============

Module Summary
--------------

``next.utils`` exposes two helpers that project code can import, ``resolve_base_dir`` and ``classify_dirs_entries``.
The rest of the module serves decorator registration and is excluded from the table below, attributing a decorated object to the file where it was declared, naming it for diagnostics, and collecting the registrations that landed on another file.

``resolve_base_dir`` returns ``settings.BASE_DIR`` coerced to ``pathlib.Path``, or ``None`` when it is unset, for backends that resolve project-relative paths.
``classify_dirs_entries`` splits a backend ``DIRS`` list into existing directory roots and plain skip-name segments, the same split the file router applies.

Public API
----------

.. automodule:: next.utils
   :members:
   :exclude-members: _classify_one_dir_entry, callable_name, defining_file, MisattributedContext, MisattributionLog

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` documents the ``DIRS`` semantics that ``classify_dirs_entries`` supports.
