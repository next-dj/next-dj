.. _ref-utils:

Utils Reference
===============

Module Summary
--------------

``next.utils`` exposes three helper functions that other parts of the framework rely on.
The helpers are stable enough that project code can import them.

``resolve_base_dir`` returns ``settings.BASE_DIR`` as a :class:`~pathlib.Path`, or ``None`` when the setting is missing or not a path.
Path-resolving helpers across the framework use it as the anchor for relative ``DIRS`` entries.

``classify_dirs_entries`` splits a backend ``DIRS`` list into absolute directory roots and bare URL segment names.
The file router calls it to interpret the ``DIRS`` shapes documented in :doc:`/content/topics/file-router`.

``caller_source_path`` helps extension code find the source file of a user-defined module when a decorator or factory in the framework sits on the call stack between Django and the project's ``page.py`` or ``component.py``.
Use it when you emit diagnostics or resolve paths relative to the author's package instead of relative to the framework.

Public API
----------

.. automodule:: next.utils
   :members:
   :exclude-members: _classify_one_dir_entry

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` documents the ``DIRS`` semantics that ``classify_dirs_entries`` supports.
