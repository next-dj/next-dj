.. _ref-utils:

Utils Reference
===============

Module Summary
--------------

``next.utils`` exposes small helper functions that other parts of the framework rely on.
The helpers are stable enough that project code can import them, but they do not power the main user facing flow.

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

   :doc:`/content/topics/project-layout` for how ``classify_dirs_entries`` is used.
