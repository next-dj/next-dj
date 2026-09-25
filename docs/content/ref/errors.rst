.. _ref-errors:

Errors reference
================

Module summary
--------------

``next.errors`` holds the exceptions a misconfigured project raises across more than one subsystem.
Every one of them subclasses :class:`~django.core.exceptions.ImproperlyConfigured`, so a project that already catches Django's configuration error keeps catching these.
An exception that belongs to one subsystem lives in that subsystem's ``errors`` module instead, such as ``next.urls.errors``, ``next.partial.errors``, or ``next.pages.errors``, which holds ``PageModuleImportError``, ``PageContextShapeError``, and the four ``PageMetadata*`` errors.
None of the six is a configuration error, so they subclass ``Exception``, ``TypeError``, and ``ValueError`` rather than the Django one, and :doc:`pages` documents all of them.

``InvalidDirsError`` is raised by ``classify_dirs_entries`` for a ``DIRS`` value that is no sequence of trees, which covers a bare string, because iterating a string would split it into one entry per character.
The remaining six report a backend the shared loader cannot turn into a class.
Each class carries the attributes of its own failure and nothing else, so a handler reads ``root_name`` and ``config`` off a ``BackendPathError`` and ``setting``, ``dotted``, and ``base_name`` off a ``SettingNotSubclassError``, and one class always carries its own set whichever loader raised it.
``BackendPathError`` names an entry whose ``BACKEND`` is no dotted path, ``BackendImportError`` an entry whose path does not import, ``BackendNotSubclassError`` a class outside the family root, and ``AbstractBackendError`` an entry that names the abstract root itself.
``SettingImportError`` and ``SettingNotSubclassError`` report the same two failures for the other settings shape, a top-level key holding one dotted path, so they name that key alongside the path.

Public API
----------

.. automodule:: next.errors
   :members:

See also
--------

.. seealso::

   :doc:`backends` for the loading helpers that raise the four backend errors.
   :doc:`utils` for ``classify_dirs_entries``, the split that raises ``InvalidDirsError``.
