.. _ref-utils:

Utils Reference
===============

Module Summary
--------------

``next.utils`` exposes two helpers that project code can import, ``resolve_base_dir`` and ``classify_dirs_entries``.
The module also defines a registration-internal frame helper that the framework uses to attribute decorated callables to their defining file.
That helper is not part of the public surface and is excluded from the autodoc table below.

``resolve_base_dir`` returns ``settings.BASE_DIR`` coerced to ``pathlib.Path``, or ``None`` when it is unset, for backends that resolve project-relative paths.
``classify_dirs_entries`` splits a backend ``DIRS`` list into existing directory roots and plain skip-name segments, the same split the file router applies.

Public API
----------

.. automodule:: next.utils
   :members:
   :exclude-members: _classify_one_dir_entry, caller_source_path

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` documents the ``DIRS`` semantics that ``classify_dirs_entries`` supports.
