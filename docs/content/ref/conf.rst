.. _ref-conf:

Configuration Reference
=======================

Module Summary
--------------

``next.conf`` merges user ``NEXT_FRAMEWORK`` settings with framework defaults.
It exposes the merged-settings object, the import helpers, the ``extend_default_backend`` helper, and the ``settings_reloaded`` signal.

Public API
----------

Settings Class
~~~~~~~~~~~~~~

.. automodule:: next.conf.settings
   :members:

Defaults
~~~~~~~~

.. automodule:: next.conf.defaults
   :members:

Helpers
~~~~~~~

.. automodule:: next.conf.helpers
   :members:

Import Utilities
~~~~~~~~~~~~~~~~

.. autofunction:: next.conf.imports.import_class_cached

.. autofunction:: next.conf.imports.perform_import

.. autofunction:: next.conf.imports.clear_import_cache

.. autodata:: next.conf.imports.IMPORT_STRINGS

Signals
-------

See :doc:`signals` for the ``settings_reloaded`` signal.

See Also
--------

.. seealso::

   :doc:`settings` for the full ``NEXT_FRAMEWORK`` key catalog.
   :doc:`/content/topics/extending` for the helper patterns.
