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

Merged values are immutable, so appending to ``next_framework_settings.PAGE_BACKENDS``, assigning into it, or mutating a nested list or mapping raises ``TypeError``.
A value of any other type, such as a set inside ``OPTIONS``, is copied rather than frozen, so it stays editable and only the copy handed to you changes.
They remain a ``list`` and a ``dict``, so ``isinstance`` checks, equality against a plain container, and ``json.dumps`` keep working, and only mutation is refused.
The concrete types are ``FrozenList`` and ``FrozenDict`` from ``next.conf.frozen``, which is framework-internal and carries no stability guarantee.
The guard covers ordinary mutation rather than a determined caller, because an unbound call such as ``list.append(value, item)`` still reaches the underlying container.

To change a value, change ``settings.NEXT_FRAMEWORK`` and call ``next_framework_settings.reload()``, which Django's ``override_settings`` already does on entry and exit.
A ``NEXT_FRAMEWORK`` value that refers back to itself cannot be frozen and raises :exc:`~django.core.exceptions.ImproperlyConfigured` naming the self-referential value.

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
