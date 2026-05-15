.. _ref-deps:

Dependency Injection Reference
==============================

Module Summary
--------------

``next.deps`` exposes the resolver, the parameter providers, the dependency cache, and the public markers used in annotations.

Public API
----------

Resolver
~~~~~~~~

.. automodule:: next.deps.resolver
   :members:
   :exclude-members: resolver

Providers
~~~~~~~~~

.. automodule:: next.deps.providers
   :members:

Markers
~~~~~~~

.. automodule:: next.deps.markers
   :members:

Cache
~~~~~

.. automodule:: next.deps.cache
   :members:

Context
~~~~~~~

.. automodule:: next.deps.context
   :members:

Signals
-------

See :doc:`signals` for the ``provider_registered`` signal.

See Also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for the topic guide.
   :doc:`/content/internals/di-resolver` for the resolver internals.
