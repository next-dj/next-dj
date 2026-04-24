.. _api-reference:

API Reference
=============

This page is an index into the per-subsystem API pages. Each subsystem re-exports a narrow public surface from its submodules. For the full list of public names follow the link below each section.

Pages (next.pages)
------------------

Page rendering, template loaders, context, and layout management. See :doc:`/content/guide/pages-and-templates` for a narrative walkthrough.

.. toctree::

   pages

Components (next.components)
----------------------------

Scoped reusable template fragments, backends, and rendering helpers. See :doc:`/content/guide/components` for usage, settings, and examples.

.. toctree::

   components

Static assets (next.static)
---------------------------

Co-located CSS/JS discovery, staticfiles integration via
``next.static.NextStaticFilesFinder``, and the ``{% collect_styles %}`` /
``{% collect_scripts %}`` / ``{% use_style %}`` / ``{% use_script %}``
template tags. See :doc:`/content/guide/static-assets`
for usage, settings, and examples.

.. toctree::

   static

URLs and routing (next.urls)
----------------------------

File-based URL pattern generation and router backends. See :doc:`/content/guide/file-router`.

.. toctree::

   urls

Forms (next.forms)
------------------

Form action decorator, registry-backed dispatcher, and validation-error rendering helpers. See :doc:`/content/guide/forms`.

.. toctree::

   forms

Server (next.server)
--------------------

``runserver`` integration: :class:`~next.server.NextStatReloader` watches the route set and Python entrypoints. ``.djx`` files are not watched. See :doc:`/content/guide/autoreload`.

.. toctree::

   server

Utils (next.utils)
------------------

Small reusable helpers not tied to the HTTP server or routing. The module is intentionally minimal. Add shared utilities here as needed.

.. automodule:: next.utils
   :members:
   :undoc-members:
   :show-inheritance:

Checks (next.checks)
--------------------

Django system checks for next.dj configuration.

.. automodule:: next.checks
   :members:
   :undoc-members:
   :show-inheritance:

.. _dependency-injection-api:

Dependency injection (next.deps)
--------------------------------

Dependency resolution and built-in providers for request, URL kwargs, and forms. Used in page context and form handlers (see :doc:`/content/guide/dependency-injection`).

.. toctree::

   deps

Configuration (next.conf)
-------------------------

Reads and caches ``settings.NEXT_FRAMEWORK`` and re-publishes the merged view through ``next_framework_settings``. Broadcasts ``settings_reloaded`` when Django raises ``setting_changed``.

.. toctree::

   conf

App wiring (next.apps)
----------------------

``AppConfig`` that connects the framework into Django on startup.

.. toctree::

   apps

Testing helpers (next.testing)
------------------------------

Framework-agnostic helpers for tests: ``NextClient``, ``SignalRecorder``,
registry-reset functions, and eager page loading. Works with pytest,
``django.test.TestCase``, and stdlib ``unittest``. See :doc:`/content/guide/testing`.

.. toctree::

   testing

Aggregated signals (next.signals)
---------------------------------

Flat re-export of every signal emitted by next.dj subsystems. Import
from here when wiring multiple receivers without tracking which
subsystem owns each signal.

.. toctree::

   signals

Configuration reference
-----------------------

NEXT_FRAMEWORK
~~~~~~~~~~~~~~

Single dictionary in Django settings. Top-level keys (each optional beyond defaults):

* ``DEFAULT_PAGE_BACKENDS`` is a list of file-router backend dicts (``BACKEND``, ``PAGES_DIR``, ``APP_DIRS``, ``DIRS``, ``OPTIONS``). The file router's skip-folder name always comes from ``DEFAULT_COMPONENT_BACKENDS`` (``COMPONENTS_DIR`` on the first entry). See :doc:`/content/guide/file-router`.
* ``URL_NAME_TEMPLATE`` is the format string for URL pattern names (default ``page_{name}``).
* ``DEFAULT_COMPONENT_BACKENDS`` is a list of component backend dicts (``BACKEND``, ``DIRS``, ``COMPONENTS_DIR``). See :doc:`/content/guide/components`.
* ``DEFAULT_STATIC_BACKENDS`` is a list of static backend dicts (``BACKEND``, ``OPTIONS``). The default backend resolves co-located CSS/JS through Django ``staticfiles_storage``. See :doc:`/content/guide/static-assets`.
* ``NEXT_JS_OPTIONS`` is a dict passed to :class:`~next.static.NextScriptBuilder` to control injection of ``next.min.js``. Recognised keys are ``policy`` (``"auto"``/``"disabled"``/``"manual"`` or a :class:`~next.static.ScriptInjectionPolicy` member), ``preload_template``, ``script_tag_template``, and ``init_template``. See :doc:`/content/guide/static-assets`.
* ``STRICT_CONTEXT`` (bool, default ``False``) promotes ``TypeError`` / ``ValueError`` / ``AttributeError`` / ``KeyError`` raised by Django context processors from a warning to a re-raise during page rendering. See :doc:`/content/guide/context`.
* ``LAZY_COMPONENT_MODULES`` (bool, default ``False``) skips the eager import of every discovered ``component.py`` at startup. Modules are imported on first resolve of the component instead. See :doc:`/content/guide/components`.
* ``TEMPLATE_LOADERS`` is a list of dotted paths to :class:`~next.pages.loaders.TemplateLoader` subclasses. Defaults to ``["next.pages.loaders.DjxTemplateLoader"]``. A user-provided list **replaces** the default, so include ``DjxTemplateLoader`` explicitly if you want ``.djx`` files to keep resolving. See :doc:`/content/guide/pages-and-templates`.
* ``JS_CONTEXT_SERIALIZER`` is an optional dotted path to a class implementing the :class:`~next.static.serializers.JsContextSerializer` protocol (single ``dumps(value) -> str`` method). Values from ``@context(serialize=True)`` flow through this serializer into ``window.Next.context``. Defaults to ``None`` (uses the built-in JSON encoder). See :doc:`/content/guide/static-assets`.

.. code-block:: python

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "PAGES_DIR": "pages",
               "APP_DIRS": True,
               "DIRS": [],
               "OPTIONS": {
                   "context_processors": [
                       "myapp.context_processors.global_context",
                   ],
               },
           },
       ],
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [str(BASE_DIR / "root_components")],
               "COMPONENTS_DIR": "_components",
           },
       ],
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {},
           },
       ],
   }
