.. description: System checks (manage.py check): error codes next.E### / next.W### by layer and how to fix them.

System checks
=============

next.dj registers `Django system checks`_ so ``python manage.py check`` validates
settings, file-based routes, templates, URLs, and components. Use
``python manage.py check --tag <tag>`` to run a subset (for example ``urls`` or
``templates``). To silence a specific message, set ``SILENCED_SYSTEM_CHECKS`` in
settings to the message id (for example ``"next.E012"``).

.. _Django system checks: https://docs.djangoproject.com/en/stable/topics/checks/

Tags
----

Checks use these tags (see ``register`` in ``next/checks.py``):

* **templates** — ``check_request_in_context``, ``check_layout_templates``,
  ``check_context_functions``
* **urls** — ``check_duplicate_url_parameters``, ``check_url_patterns``
* **compatibility** — ``NEXT_FRAMEWORK``, filesystem pages, components

Layers and codes
----------------

Settings and ``NEXT_FRAMEWORK``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Router list shape, backends, and ``FileRouterBackend`` fields.

.. list-table::
   :header-rows: 1
   :widths: 15 50 15

   * - Id
     - Meaning
     - Level
   * - ``next.E001``
     - ``NEXT_FRAMEWORK`` or ``DEFAULT_PAGE_BACKENDS`` has wrong top-level type
     - Error
   * - ``next.E002``
     - ``DEFAULT_PAGE_BACKENDS[i]`` must be a dict
     - Error
   * - ``next.E003``
     - ``DEFAULT_PAGE_BACKENDS[i]`` must declare ``BACKEND``
     - Error
   * - ``next.E004``
     - Unknown ``BACKEND`` string
     - Error
   * - ``next.E005`` / ``E006``
     - Wrong types for ``APP_DIRS`` / ``OPTIONS`` on file router
     - Error
   * - ``next.E022``
     - ``DEFAULT_PAGE_BACKENDS`` must contain at least one entry
     - Error
   * - ``next.E023``
     - ``DEFAULT_COMPONENT_BACKENDS`` must be a list
     - Error
   * - ``next.E024``–``E027``
     - Missing or invalid ``PAGES_DIR`` / ``APP_DIRS`` / ``DIRS`` / ``OPTIONS`` for file router
     - Error

**What to do:** Fix ``NEXT_FRAMEWORK`` in settings (see :doc:`../guide/file-router`).
Use a valid backend path and required keys for ``next.urls.FileRouterBackend``.

Templates and context processors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 15 50 15

   * - Id
     - Meaning
     - Level
   * - ``next.E019``
     - With ``next`` installed, each ``TEMPLATES[i]`` should include
       ``django.template.context_processors.request``
     - Error

**What to do:** Add the processor under ``OPTIONS['context_processors']`` for each
engine you use. See :doc:`../guide/pages-and-templates`.

Filesystem pages and ``page.py``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Directory segments, ``page.py``, ``render`` / template, and empty-page warning.

.. list-table::
   :header-rows: 1
   :widths: 15 50 15

   * - Id
     - Meaning
     - Level
   * - ``next.E007``
     - Could not initialize ``RouterManager`` (import/config)
     - Error
   * - ``next.E030``
     - Error while walking router page trees
     - Error
   * - ``next.E008`` / ``E009``
     - Invalid or incomplete ``[param]`` / ``[[args]]`` directory names
     - Error
   * - ``next.E010``
     - Dynamic segment directory missing ``page.py``
     - Error
   * - ``next.E011``
     - Error while validating page functions under a router
     - Error
   * - ``next.E012`` / ``E013``
     - Missing render/template path, or ``render`` not callable
     - Error
   * - ``next.W001``
     - ``layout.djx`` missing ``{% block template %}``
     - Warning
   * - ``next.W002``
     - ``page.py`` has no template, render, ``template.djx``, or ``layout.djx``
     - Warning
   * - .. _check-next-w043:

       ``next.W043``
     - ``page.py`` declares more than one body source
       (``render()`` / ``template`` / ``template.djx``).
       Priority order: ``render() > template > template.djx``.
       The lower-priority sources are silently dropped at render time.
     - Warning

**What to do:** Follow :doc:`../guide/file-router` and :doc:`../guide/pages-and-templates`.
Add ``render`` or ``template`` / ``template.djx``. Fix bracket folder names. Use
``layout.djx`` with the required block when using layouts. For W043, remove the
redundant body sources so only the one you actually use remains on disk.

URL patterns
~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 15 50 15

   * - Id
     - Meaning
     - Level
   * - ``next.E014``
     - Exception while checking generated URL conflicts
     - Error
   * - ``next.E015``
     - Same Django path string produced from more than one page
     - Error
   * - ``next.E016``
     - Exception while collecting patterns from a router
     - Error
   * - ``next.E028``
     - Duplicate parameter names inside one URL (for example two ``[id]``)
     - Error

**What to do:** Rename segments so names are unique. Resolve duplicate routes by
moving or renaming ``page.py`` files so generated patterns differ.

Components
~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 15 50 15

   * - Id
     - Meaning
     - Level
   * - ``next.E020``
     - Same component name registered twice in one scope
     - Error
   * - ``next.E021``
     - ``component.py`` must not import ``context`` from ``next.pages``
     - Error

**What to do:** Rename or relocate components. Use ``next.components`` context APIs
(see :doc:`../guide/components`).

Context functions on pages
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 15 50 15

   * - Id
     - Meaning
     - Level
   * - ``next.E029``
     - ``@context`` without a key must return a ``dict``
     - Error

**What to do:** Return a mapping from context helpers, or use ``@context("name")``.
See :doc:`../guide/context`.

Forms
~~~~~

Raised directly from :mod:`next.forms.backends` at registration time (``@forms.action``).

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Error
     - Meaning
   * - ``ImproperlyConfigured: Form action UID collision``
     - Two distinct action names hash to the same 16-character UID. Rename one
       of them, or pass ``namespace="…"`` on ``@forms.action`` to add a
       disambiguating prefix. See :doc:`../guide/forms`.

Reported by ``manage.py check``:

.. list-table::
   :header-rows: 1
   :widths: 15 50 15

   * - Id
     - Meaning
     - Level
   * - ``next.E040``
     - Context processor in ``OPTIONS['context_processors']`` has no
       ``request`` parameter
     - Error
   * - ``next.E041``
     - Two ``@action`` calls register distinct handlers for the same action name
     - Error
   * - ``next.W042``
     - ``NEXT_FRAMEWORK['JS_CONTEXT_SERIALIZER']`` does not resolve to a class
       implementing the ``JsContextSerializer`` protocol
     - Warning
   * - ``next.E042``
     - ``NEXT_FRAMEWORK['TEMPLATE_LOADERS']`` entry is not a dotted path string
     - Error
   * - ``next.E043``
     - ``NEXT_FRAMEWORK['TEMPLATE_LOADERS']`` entry cannot be imported or
       does not subclass ``next.pages.loaders.TemplateLoader``
     - Error

**What to do:** E040 — ensure context processor callables accept ``request``.
E041 — rename one of the colliding handlers or add ``namespace="…"``. W042 —
point ``JS_CONTEXT_SERIALIZER`` at a class providing ``dumps(value) -> str``,
for example :class:`~next.static.serializers.JsonJsContextSerializer`. E042 /
E043 — every entry in ``TEMPLATE_LOADERS`` must be a dotted path that imports
to a :class:`~next.pages.loaders.TemplateLoader` subclass.

``NEXT_FRAMEWORK`` keys (reference)
------------------------------------

Flat top-level keys (see :mod:`next.conf`):

* ``DEFAULT_PAGE_BACKENDS`` — list of file-router backend dicts (``BACKEND``, ``PAGES_DIR``, ``APP_DIRS``, ``DIRS``, ``OPTIONS``, …). The skip-folder name for routing comes from ``DEFAULT_COMPONENT_BACKENDS``, not from page router dicts.
* ``URL_NAME_TEMPLATE`` — Python format string for URL names (default ``page_{name}``).
* ``DEFAULT_COMPONENT_BACKENDS`` — list of component backend dicts.
* ``TEMPLATE_LOADERS`` — list of dotted paths to :class:`~next.pages.loaders.TemplateLoader` subclasses. Defaults to ``["next.pages.loaders.DjxTemplateLoader"]``; user lists replace the default.

Each key can be overridden independently. There is no nested ``PAGES`` / ``COMPONENTS`` namespace and no runtime hook for custom ``ModuleLoader`` classes.
