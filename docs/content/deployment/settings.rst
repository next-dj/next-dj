.. _deployment-settings:

Production settings
===================

This page lists recommended ``NEXT_FRAMEWORK`` values for production.
Each entry explains why the production value differs from the development default.
For the full list of available keys, their defaults, and their semantics, see :doc:`/content/ref/settings`.

Each snippet below sets one key on an existing ``NEXT_FRAMEWORK`` dict.
Declare ``NEXT_FRAMEWORK = {}`` once before the first override, or merge the keys into a single literal as shown under :ref:`combining-keys`.
Keys left unset keep their framework default, because the project dict merges over the framework defaults one level deep and a key the project leaves out is never replaced, see :ref:`ref-settings-merge`.

Strict context
--------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {}
   NEXT_FRAMEWORK["STRICT_CONTEXT"] = True

Set ``STRICT_CONTEXT`` to ``True`` in production so a misconfigured context processor fails loudly.
See :ref:`ref-settings` for behaviour and exception types.

Strict loading
--------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK["STRICT_LOADING"] = True

Set ``STRICT_LOADING`` to ``True`` in production so a ``page.py`` that fails to import or a ``{% component %}`` name that does not resolve fails the request instead of serving a silently degraded page.
With ``DEBUG=False`` the client sees the generic 500 page, and the traceback appears only in the server log through ``logger.exception``.
Without the flag a broken ``page.py`` answers a generic 404 and a missed component renders as an empty string, which monitoring rarely catches.
See :ref:`ref-settings` for the loudness table across ``DEBUG`` and the strict flags.

Component module loading
------------------------

``LAZY_COMPONENT_MODULES`` already defaults to ``False``, which is the production value, so a deployment writes the key only to turn lazy loading on.
The framework discovers the component tree eagerly in both modes, so the registry knows every component name before traffic.
The flag controls only when each ``component.py`` module is imported.
With the default ``False``, every ``component.py`` is imported during startup, so any import-time error surfaces before the first request.
With ``True``, a ``component.py`` is imported on the first render that resolves the component rather than during startup.
See :ref:`ref-settings` and :doc:`/content/topics/testing` for lazy behaviour and testing helpers.

Static backend
--------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK["STATIC_BACKENDS"] = [
       {"BACKEND": "notes.backends.TenantPrefixStaticBackend", "OPTIONS": {}},
   ]

Point at a custom backend where the asset URL varies per request, for example a per-tenant prefix.
A single CDN host in front of the static origin belongs in ``STATIC_URL`` instead, where every rendered path agrees with it, see :doc:`static-files`.
The default ``StaticFilesBackend`` is appropriate for single host deployments where the same process serves both HTML and static files.

Asset version
-------------

.. code-block:: python
   :caption: config/settings.py

   import os

   NEXT_FRAMEWORK["STATIC_VERSION"] = os.environ["BUILD_ID"]

Set a version only for a deployment that cannot run a hashed staticfiles manifest.
The value sets a ``v`` query parameter on every rendered asset URL, so a deploy invalidates the whole asset set at once, while a manifest reissues the URL of a changed file alone.
Read the value from the environment, because a value generated at startup differs per worker and makes a client refetch one file once per process.

JS context serializer
---------------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"] = "next.static.PydanticJsContextSerializer"

Set the serializer when context values include types beyond the standard JSON set.
``PydanticJsContextSerializer`` handles Pydantic models and falls back to the Django JSON encoder for plain values.

.. note::

   ``PydanticJsContextSerializer`` requires the ``pydantic`` package, which is not a dependency of next.dj.
   Install it separately (``pip install pydantic``) before enabling this serializer.
   If ``pydantic`` is not installed, the first render that serializes context raises ``ImportError``.

Page backends with context processors
-------------------------------------

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK["PAGE_BACKENDS"] = extend_default_backend(
       "PAGE_BACKENDS",
       OPTIONS={"context_processors": [
           "notes.context_processors.csp_nonce",
           "notes.context_processors.tenant",
       ]},
   )

Use ``extend_default_backend`` to patch the default page backend entry with production context processors.
The ``OPTIONS`` dict is merged, so the other default keys survive.

Form action backend
-------------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK["FORM_ACTION_BACKENDS"] = [
       {"BACKEND": "notes.backends.RateLimitedFormActionBackend"},
   ]

Register a custom backend that subclasses ``RegistryFormActionBackend`` and rate limits dispatch for endpoints exposed to anonymous users.
See :doc:`/content/howto/write-a-form-action-backend`.

.. _combining-keys:

Combining keys
--------------

When several recommendations apply at once, merge them into a single ``NEXT_FRAMEWORK`` literal.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "STRICT_CONTEXT": True,
       "STRICT_LOADING": True,
       "JS_CONTEXT_SERIALIZER": "next.static.PydanticJsContextSerializer",
       "PAGE_BACKENDS": extend_default_backend(
           "PAGE_BACKENDS",
           OPTIONS={"context_processors": [
               "notes.context_processors.csp_nonce",
               "notes.context_processors.tenant",
           ]},
       ),
       "FORM_ACTION_BACKENDS": [
           {"BACKEND": "notes.backends.RateLimitedFormActionBackend"},
       ],
   }

Keep only the keys the deployment changes.
The framework supplies the default for every key left out, so there is no need to duplicate the full default structures documented on :doc:`/content/ref/settings`.

Runtime script overrides
------------------------

Strict content security policies sometimes need nonces or manual ordering for the bundled ``next.min.js`` shell.
``NEXT_FRAMEWORK["NEXT_JS_OPTIONS"]`` accepts template overrides and ``ScriptInjectionPolicy`` values described on :ref:`ref-settings` and in :doc:`/content/topics/static-assets/js-context`.

Template and asset staleness
----------------------------

No ``NEXT_FRAMEWORK`` key controls this one, ``settings.DEBUG`` does, and it is the only behaviour difference between a warm cache and a cold one.

The composed-template cache, the compiled component template cache, and the co-located asset plans each hold a snapshot of what they read from disk.
Every probe that compares that snapshot against the disk is gated on ``settings.DEBUG`` through the ``next.utils.template_edits_watched`` predicate.
With ``DEBUG`` off none of them runs, and a warm request issues no ``stat`` call at all.

The consequence is that a running production process never notices a file changed underneath it.
An edited ``template.djx``, an edited or newly added ``layout.djx``, and a ``template.css`` created next to a page are all invisible until the process restarts.
Only a registration still invalidates an asset plan, because a stem or a kind registered at startup moves no file and is compared through a generation counter rather than a ``stat``.

This is a deliberate trade of edit visibility for a syscall-free hot path.
A deployment publishes assets through ``collectstatic`` and restarts its workers, so the alternative buys nothing a release does not already do.
Read :doc:`/content/internals/page-discovery` and :doc:`/content/internals/static-pipeline` for the snapshots and the checks that read them.

See also
--------

.. seealso::

   :doc:`checklist` for the full pre-flight list.
   :doc:`/content/ref/settings` for every available key.
