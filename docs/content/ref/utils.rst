.. _ref-utils:

Utils Reference
===============

Module Summary
--------------

``next.utils`` exposes two helpers that project code can import: ``resolve_base_dir`` and ``classify_dirs_entries``.
``caller_source_path`` is a registration-internal frame helper the framework uses to attribute a decorated callable to its defining file, documented here for contributors rather than for project use.

Public API
----------

.. automodule:: next.utils
   :members:
   :exclude-members: _classify_one_dir_entry

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` documents the ``DIRS`` semantics that ``classify_dirs_entries`` supports.
