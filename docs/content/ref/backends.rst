.. _ref-backends:

Backends reference
==================

Module summary
--------------

``next.backends`` holds the shared loading and lazy management helpers behind every settings-driven backend family.
``load_backends`` instantiates a configured backend list, ``resolve_backend_class`` resolves one dotted ``BACKEND`` path against a family root, and ``SingleBackendManager`` lazily builds the single backend named by one settings key.
``backend_entries`` returns the dict entries under one list-valued framework settings key, dropping any entry that is not a dict.
``BackendRoot`` is the type alias each family uses to pass its abstract root class to these helpers.

The two loading paths differ in how they treat a misconfigured entry.
``load_backends`` logs and skips it, so the family keeps serving with the remaining backends.
``SingleBackendManager`` raises :class:`~django.core.exceptions.ImproperlyConfigured` instead, because a family with one backend has nothing to fall back to.

Public API
----------

.. autofunction:: next.backends.load_backends

.. autofunction:: next.backends.backend_entries

.. autofunction:: next.backends.resolve_backend_class

.. autoclass:: next.backends.SingleBackendManager
   :members:

.. autodata:: next.backends.BackendRoot

See also
--------

.. seealso::

   :doc:`settings` for the ``*_BACKENDS`` and ``*_BACKEND`` keys these helpers read.
   :doc:`/content/topics/extending` for writing a custom backend.
