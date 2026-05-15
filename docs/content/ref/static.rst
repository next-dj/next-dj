.. _ref-static:

Static Reference
================

Module Summary
--------------

``next.static`` exposes the asset discovery, the request scoped collector, the backend chain, the kind registry, and the JS context serializer.

Public API
----------

Collector
~~~~~~~~~

.. automodule:: next.static.collector
   :members:

Discovery
~~~~~~~~~

.. automodule:: next.static.discovery
   :members:

Backends
~~~~~~~~

.. automodule:: next.static.backends
   :members:

Assets
~~~~~~

.. automodule:: next.static.assets
   :members:

Manager
~~~~~~~

.. automodule:: next.static.manager
   :members:

Scripts
~~~~~~~

.. automodule:: next.static.scripts
   :members:

JS Context Serializer
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.static.serializers
   :members:

Defaults
~~~~~~~~

.. automodule:: next.static.defaults
   :members:

Signals
-------

See :doc:`signals` and :doc:`/content/topics/static-assets/signals` for the static signals (``asset_registered``, ``collector_finalized``, ``html_injected``, ``backend_loaded``).

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/index` for the topic subtree.
   :doc:`/content/internals/static-pipeline` for the internal flow.
