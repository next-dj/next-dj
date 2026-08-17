.. _ref-conf:

Configuration reference
=======================

Module summary
--------------

``next.conf`` merges user ``NEXT_FRAMEWORK`` settings with framework defaults.
It exposes the merged-settings object, the ``DEFAULTS`` mapping and the ``USER_SETTING`` name, the import helpers, the ``extend_default_backend`` helper, the ``fail_loudly`` predicate that every fail-loud path reads, and the ``settings_reloaded`` signal.

Public API
----------

Settings class
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

Import utilities
~~~~~~~~~~~~~~~~

.. autofunction:: next.conf.imports.import_class_cached

.. autofunction:: next.conf.imports.perform_import

``next.conf.imports.clear_import_cache`` is framework-internal.
The settings object invokes it from ``reload`` to drop cached imports when settings change.

.. autodata:: next.conf.imports.IMPORT_STRINGS

Signals
-------

See :doc:`signals` for the ``settings_reloaded`` signal.

See also
--------

.. seealso::

   :doc:`settings` for the full ``NEXT_FRAMEWORK`` key catalog.
   :doc:`/content/topics/extending` for the helper patterns.
