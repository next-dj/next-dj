.. _ref-errors:

Errors reference
================

Module summary
--------------

``next.errors`` holds the exceptions a misconfigured project raises across more than one subsystem.
Every one of them but ``DurationError`` subclasses :class:`~django.core.exceptions.ImproperlyConfigured`, so a project that already catches Django's configuration error keeps catching these.
An exception that belongs to one subsystem lives in that subsystem's ``errors`` module instead, such as ``next.urls.errors``, ``next.partial.errors``, or ``next.pages.errors``, which holds ``PageModuleImportError``, ``PageContextShapeError``, and the four ``PageMetadata*`` errors.
None of those six page errors is a configuration error, so they subclass ``Exception``, ``TypeError``, and ``ValueError`` rather than the Django one, and :doc:`pages` documents all of them.

``InvalidDirsError`` is raised by ``classify_dirs_entries`` for a ``DIRS`` value that is no sequence of trees, which covers a bare string, because iterating a string would split it into one entry per character.
``DurationError`` is a ``ValueError`` that ``parse_duration`` of ``next.utils`` raises for a duration that is no number of seconds or ``"2s"`` literal from zero up to one day, and each reader of a duration setting or option turns it into its own error.
``AsyncCallableError`` is raised when a ``render``, a context callable, an action handler, or a form hook is ``async def``, and carries the ``role`` and the described ``subject``.
Six more classes of ``next.errors`` report a backend the shared loader cannot turn into a class.
Each class carries the attributes of its own failure and nothing else, so a handler reads ``root_name`` and ``config`` off a ``BackendPathError`` and ``setting``, ``dotted``, and ``base_name`` off a ``SettingNotSubclassError``, and one class always carries its own set whichever loader raised it.
``BackendPathError`` names an entry whose ``BACKEND`` is no dotted path, ``BackendImportError`` an entry whose path does not import, ``BackendNotSubclassError`` a class outside the family root, and ``AbstractBackendError`` an entry that names the abstract root itself or a subclass that still leaves an abstract method open.
``AbstractBackendError`` lists those methods in its message and, sorted, on its ``missing`` attribute beside ``dotted``.
``SettingImportError`` and ``SettingNotSubclassError`` report the same two failures for the other settings shape, a key holding one dotted path, so they name that key alongside the path, and ``scope`` names the mapping setting holding it, ``METADATA`` for the metadata renderer and ``None`` for a top-level key.
``SettingError`` is the base of the errors a system check names as an unusable ``NEXT_FRAMEWORK`` value, ``SettingImportError``, ``SettingNotSubclassError``, and ``SettingShapeError``.
A framework check that meets one yields to the check of that value, see :doc:`system-checks`.
``SettingShapeError`` reports a value outside the shape its key takes, one sentence per value on its ``problems`` attribute, each naming the key, the value found, and the shape it takes.
The first read of a scope raises it for a value inside the scope, and every request raises it while a top-level value has a type its key does not take, see :doc:`settings`.

Public API
----------

.. automodule:: next.errors
   :members:

See also
--------

.. seealso::

   :doc:`backends` for the loading helpers that raise the six backend errors.
   :doc:`utils` for ``classify_dirs_entries``, the split that raises ``InvalidDirsError``.
