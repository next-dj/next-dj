.. _deployment-settings:

Production Settings
===================

This page lists recommended ``NEXT_FRAMEWORK`` values for production.
Each entry explains why the production value differs from the development default.
For the full list of available keys, their defaults, and their semantics, see :doc:`/content/ref/settings`.

Each snippet below sets one key on an existing ``NEXT_FRAMEWORK`` dict.
Declare ``NEXT_FRAMEWORK = {}`` once before the first override, or merge the keys into a single literal as shown under :ref:`combining-keys`.

Strict Context
--------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK["STRICT_CONTEXT"] = True

Use ``STRICT_CONTEXT: True`` in production so a misconfigured context processor fails loudly.
Reference for behaviour and exception types: :ref:`ref-settings`.

Eager Component Loading
-----------------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK["LAZY_COMPONENT_MODULES"] = False

Keep ``LAZY_COMPONENT_MODULES: False`` (the default) in production so every ``component.py`` under configured component roots runs during startup and registrations exist before traffic.
When ``True``, imports from those roots defer until first resolve.
``_components`` folders beside routes still load their ``component.py`` files while URL patterns are built on first resolver access.
Reference for lazy behaviour and testing helpers: :ref:`ref-settings` and :doc:`/content/topics/testing`.

Static Backend
--------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK["DEFAULT_STATIC_BACKENDS"] = [
       {"BACKEND": "notes.backends.CdnBackend", "OPTIONS": {}},
   ]

Point at a CDN aware backend in production.
The default ``StaticFilesBackend`` is appropriate for single host deployments where the same process serves both HTML and static files.

JS Context Serializer
---------------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"] = "next.static.PydanticJsContextSerializer"

Set the serializer when context values include types beyond the standard JSON set.
``PydanticJsContextSerializer`` handles Pydantic models and falls back to the Django JSON encoder for plain values.

.. note::

   ``PydanticJsContextSerializer`` requires the ``pydantic`` package, which is not a dependency of next.dj.
   Install it separately (``pip install pydantic``) before enabling this serializer.
   If ``pydantic`` is not installed, importing the serializer raises ``ImportError`` at startup.

Page Backends With Context Processors
-------------------------------------

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK["DEFAULT_PAGE_BACKENDS"] = extend_default_backend(
       "DEFAULT_PAGE_BACKENDS",
       OPTIONS={"context_processors": [
           "notes.context_processors.csp_nonce",
           "notes.context_processors.tenant",
       ]},
   )

Use ``extend_default_backend`` to patch the default page backend entry with production context processors.
The ``OPTIONS`` dict is merged, so the other default keys survive.

Form Action Backend
-------------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK["DEFAULT_FORM_ACTION_BACKENDS"] = [
       {"BACKEND": "notes.backends.RateLimitedFormActionBackend"},
   ]

Register a custom backend that subclasses ``RegistryFormActionBackend`` and rate limits dispatch for endpoints exposed to anonymous users.
See :doc:`/content/howto/write-a-form-action-backend`.

.. _combining-keys:

Combining Keys
--------------

When several recommendations apply at once, merge them into a single ``NEXT_FRAMEWORK`` literal.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "STRICT_CONTEXT": True,
       "LAZY_COMPONENT_MODULES": False,
       "DEFAULT_STATIC_BACKENDS": [
           {"BACKEND": "notes.backends.CdnBackend", "OPTIONS": {}},
       ],
       "JS_CONTEXT_SERIALIZER": "next.static.PydanticJsContextSerializer",
       "DEFAULT_PAGE_BACKENDS": extend_default_backend(
           "DEFAULT_PAGE_BACKENDS",
           OPTIONS={"context_processors": [
               "notes.context_processors.csp_nonce",
               "notes.context_processors.tenant",
           ]},
       ),
       "DEFAULT_FORM_ACTION_BACKENDS": [
           {"BACKEND": "notes.backends.RateLimitedFormActionBackend"},
       ],
   }

Keep only the keys the deployment changes.
The framework supplies the default for every key left out, so there is no need to duplicate the full default structures documented on :doc:`/content/ref/settings`.

Runtime script overrides
------------------------

Strict content security policies sometimes need nonces or manual ordering for the bundled ``next.min.js`` shell.
``NEXT_FRAMEWORK["NEXT_JS_OPTIONS"]`` accepts template overrides and ``ScriptInjectionPolicy`` values described on :ref:`ref-settings` and in :doc:`/content/topics/static-assets/js-context`.

See Also
--------

.. seealso::

   :doc:`checklist` for the full pre-flight list.
   :doc:`/content/ref/settings` for every available key.
