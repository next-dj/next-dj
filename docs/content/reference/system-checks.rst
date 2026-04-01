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
     - Missing or invalid ``PAGES_DIR`` / ``APP_DIRS`` / ``DIRS`` / ``COMPONENTS_DIR`` / ``OPTIONS`` for file router
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

**What to do:** Follow :doc:`../guide/file-router` and :doc:`../guide/pages-and-templates`.
Add ``render`` or ``template`` / ``template.djx``. Fix bracket folder names. Use
``layout.djx`` with the required block when using layouts.

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

``NEXT_FRAMEWORK`` keys (reference)
------------------------------------

Flat top-level keys (see :mod:`next.conf`):

* ``DEFAULT_PAGE_BACKENDS`` — list of file-router backend dicts (``BACKEND``, ``PAGES_DIR``, ``APP_DIRS``, ``DIRS``, ``OPTIONS``, …). Optional ``COMPONENTS_DIR`` overrides the folder name taken from ``DEFAULT_COMPONENT_BACKENDS``.
* ``URL_NAME_TEMPLATE`` — Python format string for URL names (default ``page_{name}``).
* ``DEFAULT_COMPONENT_BACKENDS`` — list of component backend dicts.

Each key can be overridden independently. There is no nested ``PAGES`` / ``COMPONENTS`` namespace and no runtime hook for custom ``ModuleLoader`` classes.
