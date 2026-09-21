.. _ref-conf:

Configuration reference
=======================

Module summary
--------------

``next.conf`` merges user ``NEXT_FRAMEWORK`` settings with framework defaults.
It exposes the merged-settings object, the ``DEFAULTS`` mapping and the ``USER_SETTING`` name, the cached import helper, the ``extend_default_backend`` helper, the ``fail_loudly`` predicate that every fail-loud path reads, and the ``settings_reloaded`` signal.

Public API
----------

Settings class
~~~~~~~~~~~~~~

.. automodule:: next.conf.settings
   :members:

The merge policy the class applies lives in ``next.conf.merge``, which is framework-internal.
:ref:`ref-settings-merge` states the rule a project writes settings against.

Merged values are immutable, so appending to ``next_framework_settings.PAGE_BACKENDS``, assigning into it, or mutating a nested list or mapping raises ``TypeError``.
A value of any other type, such as a set inside ``OPTIONS``, is copied rather than frozen, so it stays editable and only the copy handed to you changes.
They remain a ``list`` and a ``dict``, so ``isinstance`` checks, equality against a plain container, and ``json.dumps`` keep working, and only mutation is refused.
The concrete types are ``FrozenList`` and ``FrozenDict`` from ``next.conf.frozen``, which is framework-internal and carries no stability guarantee.
The guard covers ordinary mutation rather than a determined caller, because an unbound call such as ``list.append(value, item)`` still reaches the underlying container.

Rebinding a key on the settings object is refused as well, and by a different exception.
``next_framework_settings.PAGE_BACKENDS = [...]``, or an assignment to any other declared key, raises ``AttributeError`` naming ``settings.NEXT_FRAMEWORK`` as the place to change, where mutating a container a read handed back raises ``TypeError``.

To change a value, change ``settings.NEXT_FRAMEWORK`` and call ``next_framework_settings.reload()``, which Django's ``override_settings`` already does on entry and exit.
A ``NEXT_FRAMEWORK`` value that refers back to itself cannot be frozen and raises :exc:`~django.core.exceptions.ImproperlyConfigured` naming the self-referential value.

To edit a value of your own instead, copy the merged one first.
``list()`` and ``dict()`` thaw the top level, :func:`copy.copy` returns the same kind of shallow plain container that those two build, and :func:`copy.deepcopy` thaws every level below it.
Pickling round-trips a merged value frozen.

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

``next.conf.imports.clear_import_cache`` is framework-internal.
The settings object invokes it from ``reload`` to drop cached imports when settings change.

Signals
-------

See :doc:`signals` for the ``settings_reloaded`` signal.

``settings_reloaded`` is the one framework signal sent robustly, and its contract is that every receiver runs whatever any other one does.
A receiver that raises never stops the fan-out, because each of the others still has caches of its own to drop and a half-reloaded process is worse than a loud one.
The first exception is re-raised once the send finishes, and every later one is logged through ``next.conf.signals`` with the receiver named and its traceback attached.
That invariant is what makes ``override_settings`` safe in a suite, since the enter and the exit both leave every registry consistent with the settings in force even when one receiver is broken.

See also
--------

.. seealso::

   :doc:`settings` for the full ``NEXT_FRAMEWORK`` key catalog.
   :doc:`/content/topics/extending` for the helper patterns.
