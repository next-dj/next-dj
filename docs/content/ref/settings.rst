.. _ref-settings:

Settings
========

Module Summary
--------------

This page lists every key inside ``NEXT_FRAMEWORK`` with its framework default and a short description.
Set ``NEXT_FRAMEWORK`` in ``settings.py`` to override any of these values.

For production-specific recommendations (which values to change and why), see :doc:`/content/deployment/settings`.

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

``DIRS`` accepts two kinds of entry.
An absolute or project-relative path that resolves to an existing directory is added as an extra page root.
A plain string that does not resolve to a directory is treated as a skip name: the router will not enter any directory with that name during the file walk.

See :doc:`/content/topics/file-router` for the full semantics including examples.

DEFAULT_COMPONENT_BACKENDS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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

The first static backend's ``OPTIONS`` dict accepts ``JS_CONTEXT_POLICY``, a dotted path to a conflict-resolution class the static manager applies when two context functions publish the same key for serialisation.
See :doc:`/content/topics/static-assets/js-context` under *Key Conflict Policy* for the available policies and an example.

The same ``OPTIONS`` dict accepts ``DEDUP_STRATEGY``, a dotted path to a dedup strategy class the collector instantiates once per request to drop assets several components register more than once.
See :doc:`/content/topics/static-assets/deduplication` for the bundled strategies and the custom-strategy protocol.

DEFAULT_FORM_ACTION_BACKENDS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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
~~~~~~~~~~~~~~~~~

Dict passed to ``NextScriptBuilder.from_options`` for the bundled ``next.min.js`` runtime: injection ``policy`` (``auto``, ``disabled``, or ``manual``), and optional string templates ``preload_template``, ``script_tag_template``, and ``init_template``.

Default value ``{}`` (automatic injection with default templates).

Serialisation of ``window.Next.context`` is controlled by ``JS_CONTEXT_SERIALIZER`` and by ``@context(..., serialize=True)``, not by this dict.

See :doc:`/content/topics/static-assets/js-context` under *Runtime script options* for the full table and examples.

JS_CONTEXT_SERIALIZER
~~~~~~~~~~~~~~~~~~~~~~~

Dotted path to a class that implements the ``JsContextSerializer`` protocol.
The class is instantiated with no arguments and its ``dumps`` method encodes every value bound for ``window.Next.context``.

Default value ``None``, which selects the built-in ``JsonJsContextSerializer``.

``resolve_serializer`` reads this setting on every call, so ``override_settings`` takes effect without a restart.
A value that does not resolve to a usable serializer triggers the ``next.W042`` warning during ``manage.py check`` and the framework falls back to ``JsonJsContextSerializer``.

See :doc:`static` under *JS Context Serializer* for the protocol and the bundled serializers.

Strictness
----------

STRICT_CONTEXT
~~~~~~~~~~~~~~

When ``True``, any exception raised by a Django context processor (``TypeError``, ``ValueError``, ``AttributeError``, or ``KeyError``) is re-raised immediately instead of being logged as a warning and swallowed.
The check applies only to processors listed under a page backend ``OPTIONS["context_processors"]``.
Context callables registered with ``@context`` always propagate their exceptions regardless of this setting.
When ``False``, the default, a failing processor is skipped so local development keeps rendering.

Default value ``False``.
:doc:`/content/deployment/settings` explains when to turn this on in production.

LAZY_COMPONENT_MODULES
~~~~~~~~~~~~~~~~~~~~~~~~

When ``False``, the default, ``next.apps.components.install`` imports every ``component.py`` found in configured component roots so ``@component.context`` and ``@action`` decorators run before the first HTTP request.

When ``True``, that bulk import is skipped.
Each ``component.py`` discovered through a configured root is imported the first time ``get_component`` resolves that component.

Components discovered through ``_components`` directories beside page files are unaffected.
The file router imports those modules as it walks the page tree, regardless of this flag.

Default value ``False``.
See :doc:`/content/deployment/settings` for production defaults.
For tests, ``eager_load_components`` imports every registered ``component.py`` even when this flag is ``True``. See :doc:`/content/topics/testing`.

Patching Defaults
-----------------

Use ``next.conf.extend_default_backend`` to patch one key of a default backend entry without copying the whole default.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": extend_default_backend(
           "DEFAULT_PAGE_BACKENDS",
           PAGES_DIR="routes",
       )
   }

The helper returns a deep copy of the default list with the entry at ``index`` (default ``0``) patched by the keyword overrides.
Nested dicts such as ``OPTIONS`` are merged.

See :doc:`conf` for the helper API and :doc:`/content/howto/extend-a-default-backend` for the recipe.

See Also
--------

.. seealso::

   :doc:`/content/topics/extending` for the broader picture.
   :doc:`/content/deployment/settings` for production tuned values.
   :doc:`/content/topics/static-assets/js-context` for ``NEXT_JS_OPTIONS`` and ``ScriptInjectionPolicy``.
