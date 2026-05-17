.. _howto-extend-backend:

Patch a Default Backend Entry
=============================

Problem
-------

You want to override one key of a default backend entry, such as ``PAGES_DIR`` or a value inside ``OPTIONS``, without copying the whole framework default into your settings.

Solution
--------

Use ``next.conf.extend_default_backend``.
The helper returns a deep copy of the default backend list with one entry patched by your overrides.

Walkthrough
-----------

Pick the backend list.

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
     - Form action dispatch.

The helper supports these four backend list settings.

Call the helper with the setting name and the keys to override.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": extend_default_backend(
           "DEFAULT_PAGE_BACKENDS",
           PAGES_DIR="routes",
       )
   }

The helper returns the default ``DEFAULT_PAGE_BACKENDS`` list with the first entry ``PAGES_DIR`` set to ``routes`` and every other key kept.

Override a Nested OPTIONS Key
-----------------------------

Nested dicts such as ``OPTIONS`` are merged, not replaced.
Adjacent keys survive.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": extend_default_backend(
           "DEFAULT_PAGE_BACKENDS",
           OPTIONS={"context_processors": ["notes.context_processors.tenant"]},
       )
   }

Patch a Specific Entry
----------------------

The ``index`` keyword selects which entry of the default list to patch.
The default is ``0``, the first entry.

.. code-block:: python
   :caption: config/settings.py

   extend_default_backend("DEFAULT_PAGE_BACKENDS", index=0, APP_DIRS=False)

When to Write the List by Hand
------------------------------

``extend_default_backend`` patches an existing default entry.
It does not add a new backend.
To register a custom backend, write the full list yourself.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_ACTION_BACKENDS": [
           {"BACKEND": "notes.backends.AuditedFormActionBackend"},
       ]
   }

A custom backend usually subclasses the default, so it already inherits every default behaviour.
See :doc:`/content/howto/write-a-form-action-backend`.

Verification
------------

Print the resolved setting from a Django shell.

.. code-block:: bash
   :caption: shell

   uv run python manage.py shell -c "
   from next.conf import next_framework_settings
   print(next_framework_settings.DEFAULT_PAGE_BACKENDS)
   "

The list shows the default entry with your overrides applied.

The helper raises ``ImproperlyConfigured`` for an unknown setting name and ``IndexError`` for an out of range ``index``.

See Also
--------

.. seealso::

   :doc:`/content/topics/extending` for the broader picture.
   :doc:`/content/ref/conf` for the public API.
