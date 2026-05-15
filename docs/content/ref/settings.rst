.. _ref-settings:

Settings
========

Module Summary
--------------

This page lists every key inside ``NEXT_FRAMEWORK`` with its framework default and a short description.
Set ``NEXT_FRAMEWORK`` in ``settings.py`` to override any of these values.

Backends
--------

DEFAULT_PAGE_BACKENDS
~~~~~~~~~~~~~~~~~~~~~

List of page backend configurations.

Default value.

.. code-block:: python

   [
       {
           "BACKEND": "next.urls.FileRouterBackend",
           "DIRS": [],
           "APP_DIRS": True,
           "PAGES_DIR": "pages",
           "OPTIONS": {"context_processors": []},
       }
   ]

Each entry is passed to the backend constructor.
Keys are ``BACKEND``, ``DIRS``, ``APP_DIRS``, ``PAGES_DIR``, and ``OPTIONS``.
See :doc:`/content/topics/file-router` for the semantics.

DEFAULT_COMPONENT_BACKENDS
~~~~~~~~~~~~~~~~~~~~~~~~~~

List of component backend configurations.

Default value.

.. code-block:: python

   [
       {
           "BACKEND": "next.components.FileComponentsBackend",
           "DIRS": [],
           "COMPONENTS_DIR": "_components",
       }
   ]

DEFAULT_STATIC_BACKENDS
~~~~~~~~~~~~~~~~~~~~~~~

List of static backend configurations.

Default value.

.. code-block:: python

   [
       {
           "BACKEND": "next.static.StaticFilesBackend",
           "OPTIONS": {},
       }
   ]

DEFAULT_FORM_ACTION_BACKENDS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

List of form action backend configurations.

Default value.

.. code-block:: python

   [
       {
           "BACKEND": "next.forms.RegistryFormActionBackend",
           "OPTIONS": {},
       }
   ]

Routing
-------

URL_NAME_TEMPLATE
~~~~~~~~~~~~~~~~~

Template used to compute URL names from directory paths.

Default value ``"page_{name}"``.

The framework normalises the path through the parser and substitutes ``{name}`` with the result.

Templates
---------

TEMPLATE_LOADERS
~~~~~~~~~~~~~~~~

List of template loader dotted paths.

Default value.

.. code-block:: python

   ["next.pages.loaders.DjxTemplateLoader"]

Loaders are consulted in order, first match wins.

JavaScript Context
------------------

NEXT_JS_OPTIONS
~~~~~~~~~~~~~~~

Dict of options that the JS context shell renders into the page.

Default value ``{}``.

JS_CONTEXT_SERIALIZER
~~~~~~~~~~~~~~~~~~~~~

Dotted path to a callable or class that converts the context map before JSON encoding.

Default value ``None`` (use the built in JSON serializer).

Strictness
----------

STRICT_CONTEXT
~~~~~~~~~~~~~~

When ``True`` the framework raises on undefined context keys.

Default value ``False``.

LAZY_COMPONENT_MODULES
~~~~~~~~~~~~~~~~~~~~~~

When ``True`` ``component.py`` modules are imported on first render rather than at startup.

Default value ``False``.

Extending Defaults
------------------

Use ``next.conf.extend_default_backend`` to add a backend without replacing the full chain.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_ACTION_BACKENDS": extend_default_backend(
           "DEFAULT_FORM_ACTION_BACKENDS",
           "notes.backends.AuditBackend",
           position="last",
       )
   }

See :doc:`conf` for the helper API and :doc:`/content/howto/extend-a-default-backend` for the recipe.

See Also
--------

.. seealso::

   :doc:`/content/topics/extending` for the broader picture.
   :doc:`/content/deployment/settings` for production tuned values.
