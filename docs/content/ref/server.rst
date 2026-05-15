.. _ref-server:

Server Reference
================

Module Summary
--------------

``next.server`` exposes the autoreload watcher and the development server hooks that the framework registers with Django.

Public API
----------

Autoreload
~~~~~~~~~~

.. automodule:: next.server.autoreload
   :members:

Watcher
~~~~~~~

.. automodule:: next.server.watcher
   :members:

Roots
~~~~~

.. automodule:: next.server.roots
   :members:

Signals
-------

See :doc:`signals` for the ``watch_specs_ready`` signal.

See Also
--------

.. seealso::

   :doc:`/content/internals/autoreload` for the reloader internals.
