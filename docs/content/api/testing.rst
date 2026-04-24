.. _api-testing:

Testing (next.testing)
======================

Framework-agnostic test helpers. Nothing in this package imports pytest,
so the same utilities work with :class:`django.test.TestCase`, stdlib
:mod:`unittest`, and pytest fixtures.

See :doc:`/content/guide/testing` for a narrative walkthrough.

Public surface
--------------

.. automodule:: next.testing
   :members:
   :undoc-members:
   :show-inheritance:

Client
------

.. automodule:: next.testing.client
   :members:
   :undoc-members:
   :show-inheritance:

Signal recording
----------------

.. automodule:: next.testing.signals
   :members:
   :undoc-members:
   :show-inheritance:

Registry isolation
------------------

.. automodule:: next.testing.isolation
   :members:
   :undoc-members:
   :show-inheritance:

Action helpers
--------------

.. automodule:: next.testing.actions
   :members:
   :undoc-members:
   :show-inheritance:

Eager loaders
-------------

.. automodule:: next.testing.loaders
   :members:
   :undoc-members:
   :show-inheritance:
