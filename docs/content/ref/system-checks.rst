.. _ref-system-checks:

System Checks
=============

Module Summary
--------------

next.dj contributes Django system checks for every subsystem.
Run them through ``uv run python manage.py check`` and the framework reports configuration mistakes with a code and a hint.

Check Registration
------------------

.. automodule:: next.checks.common
   :members:

Subsystem Checks
----------------

Pages
~~~~~

.. automodule:: next.pages.checks
   :members:

URLs
~~~~

.. automodule:: next.urls.checks
   :members:

Components
~~~~~~~~~~

.. automodule:: next.components.checks
   :members:

Forms
~~~~~

.. automodule:: next.forms.checks
   :members:

Static
~~~~~~

.. automodule:: next.static.checks
   :members:

Configuration
~~~~~~~~~~~~~

.. automodule:: next.conf.checks
   :members:

Server
~~~~~~

.. automodule:: next.server.checks
   :members:

Common Codes
------------

The codes follow the Django convention ``next.X<NN>`` where ``X`` is ``E`` for errors and ``W`` for warnings.
Inspect the source of each check for the full list of codes and the conditions that trigger them.

See Also
--------

.. seealso::

   :doc:`/content/intro/install` for the first ``manage.py check`` run.
