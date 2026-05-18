.. _ref-conf:

Configuration Reference
=======================

Module Summary
--------------

``next.conf`` exposes ``NextFrameworkSettings`` and the process-wide ``next_framework_settings`` instance, the ``DEFAULTS``, ``USER_SETTING``, and ``IMPORT_STRINGS`` constants, the ``perform_import`` and ``import_class_cached`` helpers, the ``extend_default_backend`` helper used in settings files, and the ``settings_reloaded`` signal.

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

.. automodule:: next.conf.imports
   :members:

Signals
-------

See :doc:`signals` for the ``settings_reloaded`` signal.

See Also
--------

.. seealso::

   :doc:`settings` for the full ``NEXT_FRAMEWORK`` key catalog.
   :doc:`/content/topics/extending` for the helper patterns.
