.. _ref-backends:

Backends reference
==================

Module summary
--------------

``next.backends`` holds the shared loading and lazy management helpers behind every settings-driven backend family.
``load_backends`` instantiates a configured backend list, ``resolve_backend_class`` resolves one dotted ``BACKEND`` path against a family root, ``instantiate_backend`` calls the resolved class with the entry that named it, and ``SingleBackendManager`` lazily builds the single backend named by one settings key.
``BackendListManager`` is the base every list-valued family manager is built on, holding the loaded list and reading the settings on the first access after a reset.
``backend_entries`` returns the dict entries under one list-valued framework settings key, dropping any entry that is not a dict.
``resolve_setting_class`` serves the other shape, a top-level key holding a single dotted path rather than a list of backend entries, and is what reads ``URL_RESOLVER``, ``DEPENDENCY_RESOLVER``, and ``COMPONENT_TEMPLATE_LOADER``.
``BackendRoot`` is the type alias each family uses to pass its abstract root class to these helpers.

The two loading paths differ in how they treat a misconfigured entry.
``load_backends`` logs and skips it, so the family keeps serving with the remaining backends.
``SingleBackendManager`` raises :class:`~django.core.exceptions.ImproperlyConfigured` instead, because a family with one backend has nothing to fall back to.

Announcing a load
~~~~~~~~~~~~~~~~~

``load_backends`` and ``SingleBackendManager`` both take an optional ``signal=`` keyword, and that parameter is the mechanism behind all six ``*_backend_loaded`` signals.
The loader sends the signal once per instance it built, with the resolved backend class as the sender and a copy of the settings entry as ``config`` beside the ``instance`` itself, immediately after the constructor returned and before any caller can reach the instance.
An entry ``load_backends`` skips announces nothing, because the send sits after the two guards that log and continue.

A family that passes no signal announces nothing at all.
That is the shape a manager takes when it reloads with ``notify=False``, which builds the list exactly as a notifying reload does and hands the loader ``None`` in place of its signal, so a caller reloading from inside a receiver of that signal does not re-enter it.

See :doc:`signals` for the payload the six signals share and :doc:`/content/topics/signals` for the receiver patterns.

Public API
----------

.. autofunction:: next.backends.load_backends

.. autofunction:: next.backends.backend_entries

.. autofunction:: next.backends.resolve_backend_class

.. autofunction:: next.backends.instantiate_backend

.. autofunction:: next.backends.resolve_setting_class

.. autoclass:: next.backends.BackendListManager
   :members:

.. autoclass:: next.backends.SingleBackendManager
   :members:

``BackendRoot`` spells a family root as the constructor signature every backend of that family shares, a callable taking the settings entry and returning the backend, because an abstract class does not pass as a ``type[T]``.
It is a type alias rather than a class, so it carries no members of its own.

See also
--------

.. seealso::

   :doc:`settings` for the ``*_BACKENDS``, ``*_BACKEND``, and ``*_RESOLVER`` keys these helpers read.
   :doc:`/content/topics/extending` for writing a custom backend.
