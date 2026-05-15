.. _howto-extend-backend:

Extend a Default Backend Chain
==============================

Problem
-------

You want to add one backend to a default chain without copying every framework default into your settings.

Solution
--------

Use ``next.conf.extend_default_backend``.
The helper merges with the framework defaults and lets you control the position.

Walkthrough
-----------

Pick the chain.

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - Setting key
     - Used by
   * - ``DEFAULT_PAGE_BACKENDS``
     - File router and layout chain.
   * - ``DEFAULT_COMPONENT_BACKENDS``
     - Component discovery and rendering.
   * - ``DEFAULT_STATIC_BACKENDS``
     - Static collector and asset rendering.
   * - ``DEFAULT_FORM_ACTION_BACKENDS``
     - Form dispatch pipeline.

Call the helper with the setting name and the backend you want to add.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": extend_default_backend(
           "DEFAULT_STATIC_BACKENDS",
           "notes.backends.CdnBackend",
           position="last",
       )
   }

Positions
~~~~~~~~~

The ``position`` argument accepts four values.

``first``.
   Insert at index zero.

``last``.
   Append at the end.

``before``.
   Insert directly before ``target``.

``after``.
   Insert directly after ``target``.

Provide ``target`` for ``before`` and ``after``.

.. code-block:: python
   :caption: target relative position

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_ACTION_BACKENDS": extend_default_backend(
           "DEFAULT_FORM_ACTION_BACKENDS",
           "notes.backends.AuditBackend",
           position="after",
           target="next.forms.backends.OriginPageBackend",
       )
   }

Single Backend Replacement
~~~~~~~~~~~~~~~~~~~~~~~~~~

A dict argument overrides a single key inside the first entry without touching the chain length.

.. code-block:: python
   :caption: override one key

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": extend_default_backend(
           "DEFAULT_PAGE_BACKENDS",
           {"PAGES_DIR": "screens"},
       )
   }

The helper merges the dict into the first backend entry.

Verification
------------

Print the resolved setting from a Django shell.

.. code-block:: bash
   :caption: shell

   uv run python manage.py shell -c "
   from next.conf import next_framework_settings
   print(next_framework_settings.DEFAULT_STATIC_BACKENDS)
   "

The list contains both the framework defaults and the new entry in the requested position.

See Also
--------

.. seealso::

   :doc:`/content/topics/extending` for the broader picture.
   :doc:`/content/ref/conf` for the public API.
