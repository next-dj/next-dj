.. _ref-signals:

Signals Reference
=================

Module Summary
--------------

``next.signals`` is an aggregator that re-exports every signal emitted by the framework.
Importing a signal from ``next.signals`` is equivalent to importing it from its subpackage.

Aggregated Signals
------------------

.. automodule:: next.signals
   :members:
   :imported-members:

Subpackage Signals
------------------

The aggregator simply forwards from these modules.

Pages
~~~~~

.. automodule:: next.pages.signals
   :members:

Components
~~~~~~~~~~

.. automodule:: next.components.signals
   :members:

URLs
~~~~

.. automodule:: next.urls.signals
   :members:

Forms
~~~~~

.. automodule:: next.forms.signals
   :members:

Static
~~~~~~

.. automodule:: next.static.signals
   :members:

Dependencies
~~~~~~~~~~~~

.. automodule:: next.deps.signals
   :members:

Server
~~~~~~

.. automodule:: next.server.signals
   :members:

Configuration
~~~~~~~~~~~~~

.. automodule:: next.conf.signals
   :members:

See Also
--------

.. seealso::

   :doc:`/content/topics/signals` for the catalog with payload tables.
