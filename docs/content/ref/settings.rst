.. _ref-settings:

Settings
========

Module Summary
--------------

This page lists every key inside ``NEXT_FRAMEWORK`` with its framework default and a short description.
Set ``NEXT_FRAMEWORK`` in ``settings.py`` to override any of these values.

For production-specific recommendations (which values to change and why), see :doc:`/content/deployment/settings`.

Key Naming
----------

Keys inside ``NEXT_FRAMEWORK`` carry no ``DEFAULT_`` prefix — the dict
itself is the framework defaults namespace. A plural ``*_BACKENDS`` key
holds an ordered list of sources the manager consults in order. A
singular ``*_BACKEND`` key holds the one engine for a concern. A
subsystem prefix (``PAGE_``, ``COMPONENT_``, ``STATIC_``, ``FORM_``,
``URL_``, ``TEMPLATE_``, ``JS_``) groups related keys.

Backends
--------

PAGE_BACKENDS
~~~~~~~~~~~~~

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
A plain string that does not resolve to a directory is treated as a skip name.
The router will not enter any directory with that name during the file walk.

See :doc:`/content/topics/file-router` for the full semantics including examples.

COMPONENT_BACKENDS
~~~~~~~~~~~~~~~~~~

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

STATIC_BACKENDS
~~~~~~~~~~~~~~~

List of static backend configurations.

Default value.

.. code-block:: python

   [
       {
           "BACKEND": "next.static.StaticFilesBackend",
           "OPTIONS": {},
       }
   ]

The first static backend's ``OPTIONS`` dict accepts ``JS_CONTEXT_POLICY``, a dotted path to a conflict-resolution class.
The static manager applies the policy when two context functions publish the same key for serialisation.
See :doc:`/content/topics/static-assets/js-context` under *Key Conflict Policy* for the available policies and an example.

The same ``OPTIONS`` dict accepts ``DEDUP_STRATEGY``, a dotted path to a dedup strategy class the collector instantiates once per request to drop assets several components register more than once.
See :doc:`/content/topics/static-assets/deduplication` for the bundled strategies and the custom-strategy protocol.

FORM_ACTION_BACKENDS
~~~~~~~~~~~~~~~~~~~~

List of form action backend configurations.

Default value.

.. code-block:: python

   [
       {
           "BACKEND": "next.forms.RegistryFormActionBackend",
           "OPTIONS": {},
       }
   ]

FORM_AUTODISCOVER
~~~~~~~~~~~~~~~~~

Boolean that controls whether ``NextFrameworkConfig.ready`` imports the ``forms`` submodule of every installed app on startup.

Default value ``True``.

When ``True``, shared forms declared in ``app/forms.py`` register before the first request arrives.
Set to ``False`` to disable the automatic import and manage registration manually.

FORM_ANCHOR_FILES
~~~~~~~~~~~~~~~~~

List of file basenames that receive ``page`` scope during auto-registration.
A form class declared in a file whose basename appears in this list is keyed to the absolute path of that file.
All other files produce ``shared`` scope.

Default value ``None``, which uses the built-in set ``["page.py", "component.py"]``.
Set to a list of strings to extend or replace the default set.

FORM_WIZARD_BACKEND
~~~~~~~~~~~~~~~~~~~

Single form wizard backend configuration.
The backend persists a wizard's per-step draft data between requests.

Default value.

.. code-block:: python

   {
       "BACKEND": "next.forms.wizard.SessionFormWizardBackend",
       "OPTIONS": {},
   }

The bundled ``SessionFormWizardBackend`` stores each step's cleaned data in the Django session through a typed value codec, so drafts share the durability of the session engine.
It reads no ``OPTIONS`` keys.
The bundled ``CacheFormWizardBackend`` stores drafts in the Django cache instead.
It reads two keys from ``OPTIONS``: ``CACHE_ALIAS`` names the cache to use, defaulting to ``"default"``, and ``TIMEOUT`` sets the draft expiry in seconds, defaulting to ``SESSION_COOKIE_AGE``.
Set ``BACKEND`` to a dotted path that subclasses ``FormWizardBackend`` to swap the persistence layer.
See :doc:`/content/topics/forms/wizard-backend` for the contract, the codec, and a custom backend.

Routing
-------

URL_NAME_TEMPLATE
~~~~~~~~~~~~~~~~~

Template used to compute URL names from directory paths.

Default value ``"page_{name}"``.

The framework normalises the path through the parser and substitutes ``{name}`` with the result.
Slashes, square brackets, colons, hyphens, and underscores are collapsed to a single underscore, and the leading and trailing underscores are stripped.
The directory path ``notes/[id]`` becomes ``notes_id`` and produces the URL name ``page_notes_id``.

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

Dict passed to ``NextScriptBuilder.from_options`` for the bundled ``next.min.js`` runtime.
Keys are the injection ``policy`` (``auto``, ``disabled``, or ``manual``) and the optional string templates ``preload_template``, ``script_tag_template``, and ``init_template``.

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

When ``True``, any ``TypeError``, ``ValueError``, ``AttributeError``, or ``KeyError`` raised by a Django context processor is re-raised immediately.
The default behaviour is to log a warning and swallow the exception.
The check applies only to processors listed under a page backend ``OPTIONS["context_processors"]``.
Context callables registered with ``@context`` always propagate their exceptions regardless of this setting.
When ``False``, the default, a failing processor is skipped so local development keeps rendering.

Default value ``False``.
:doc:`/content/deployment/settings` explains when to turn this on in production.

LAZY_COMPONENT_MODULES
~~~~~~~~~~~~~~~~~~~~~~~~

Controls bulk import of ``component.py`` modules in configured component roots during ``next.apps.components.install``.
When ``True``, each ``component.py`` is imported on demand the first time ``get_component`` resolves it.
Components discovered through ``_components`` directories beside page files are imported by the file router as it walks the page tree, regardless of this flag.

Default value ``False``.
See :doc:`/content/deployment/settings` for production defaults and :doc:`/content/topics/testing` for the ``eager_load_components`` helper.

Patching Defaults
-----------------

Use ``next.conf.extend_default_backend`` to patch one key of a default backend entry without copying the whole default.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": extend_default_backend(
           "PAGE_BACKENDS",
           PAGES_DIR="routes",
       )
   }

The helper returns a deep copy of the default list with the entry at ``index`` (default ``0``) patched by the keyword overrides.
Nested dicts such as ``OPTIONS`` are merged.

The helper raises ``ImproperlyConfigured`` when ``key`` is not a known backend-list setting.
It raises ``IndexError`` when ``index`` is out of range for the default list.

See :doc:`conf` for the helper API and :doc:`/content/howto/extend-a-default-backend` for the recipe.

See Also
--------

.. seealso::

   :doc:`/content/topics/extending` for the broader picture.
   :doc:`/content/deployment/settings` for production tuned values.
   :doc:`/content/topics/static-assets/js-context` for ``NEXT_JS_OPTIONS``.
   :class:`next.static.scripts.ScriptInjectionPolicy` for the policy enum.
