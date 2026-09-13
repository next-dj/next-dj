.. _ref-errors:

Errors reference
================

Module summary
--------------

``next.errors`` holds the exceptions a misconfigured project raises across more than one subsystem.
Every one of them subclasses :class:`~django.core.exceptions.ImproperlyConfigured`, so a project that already catches Django's configuration error keeps catching these.
An exception that belongs to one subsystem lives in that subsystem's ``errors`` module instead, such as ``next.urls.errors`` or ``next.partial.errors``.

``InvalidDirsError`` is raised by ``classify_dirs_entries`` for a ``DIRS`` value that is no sequence of trees, which covers a bare string, because iterating a string would split it into one entry per character.
The remaining four report a backend entry the shared loader cannot turn into a class.
``BackendPathError`` names an entry whose ``BACKEND`` is no dotted path, ``BackendImportError`` an entry whose path does not import, ``BackendNotSubclassError`` a class outside the family root, and ``AbstractBackendError`` an entry that names the abstract root itself.

Public API
----------

.. automodule:: next.errors
   :members:

See also
--------

.. seealso::

   :doc:`backends` for the loading helpers that raise the four backend errors.
   :doc:`utils` for ``classify_dirs_entries``, the split that raises ``InvalidDirsError``.
