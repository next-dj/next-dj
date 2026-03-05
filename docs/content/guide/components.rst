Components
==========

Components let you reuse template fragments (cards, headers, profiles) with props and slots. They live next to your pages in a dedicated folder and are resolved by **scope**: a template only sees components from its branch and from root-level component directories.

.. _components-routing:

Component folder and file routing
---------------------------------

The directory name used for components (e.g. ``_components``) is configured in the **file router** and in the **component backends**. That directory does **not** create URL segments: the file router skips it when scanning for ``page.py`` and ``template.djx``. Only this configured name is skipped (not every directory that starts with an underscore). See :doc:`file-router` for the routing side.

Backends and settings
---------------------

Components are provided by backends, similar to the page router. In Django settings you configure:

**NEXT_COMPONENTS** — list of backend configs:

- ``BACKEND`` — e.g. ``"next.components.FileComponentsBackend"``
- ``APP_DIRS`` (bool) — whether to scan each app’s pages tree for component folders
- ``OPTIONS``:
  - ``COMPONENTS_DIR`` (str) — name of the component folder inside each pages directory (default ``"_components"``)
  - ``COMPONENTS_DIRS`` — list of root-level directories that contain only components (global / root components)
  - ``COMPONENTS_DIR`` (single path) — one root-level component directory (alternative to ``COMPONENTS_DIRS``)

Example:

.. code-block:: python

   from pathlib import Path
   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_COMPONENTS = [
       {
           "BACKEND": "next.components.FileComponentsBackend",
           "APP_DIRS": True,
           "OPTIONS": {
               "COMPONENTS_DIR": "_components",
               "COMPONENTS_DIRS": [str(BASE_DIR / "root_components")],
           },
       },
   ]

You can register custom backends via ``ComponentsFactory.register_backend()``.

Types of components
-------------------

**Simple (single file)**

- One ``<name>.djx`` file in a component folder (e.g. ``_components/card.djx``).
- Component name = file name without extension (e.g. ``card``).
- Rendered with the given props and slots in context.

**Composite (folder)**

- A folder ``<name>/`` in a component folder (e.g. ``_components/profile/``).
- Inside:
  - ``component.djx`` — template (required for normal rendering).
  - ``component.py`` — optional: custom ``component = "..."`` string, ``render(...)``, and **component context** (see below).

Component name = folder name (e.g. ``profile``). Template loading follows the same priority as pages (e.g. ``component`` attribute in ``component.py``, else ``component.djx``).

Component context (no page context)
-----------------------------------

Context for a component is provided only through the **components** API (e.g. ``next.components``), not through ``next.pages``. It is not inherited from the page by default; it only adds variables when the component is rendered.

In ``component.py`` you **must not** use context from ``next.pages`` (e.g. ``from next.pages import context`` or ``page.context``). Use the component context API from ``next.components`` instead. This is enforced by the ``python manage.py check`` system (see :ref:`components-checks`).

Scope
-----

- **Root components** (from ``COMPONENTS_DIRS`` / ``COMPONENTS_DIR``) are visible from every template.
- **App/local components** (from ``_components`` under a pages directory) are visible only to templates under that directory (that page and its nested routes). Sibling branches do not see each other’s local components.

So for a template at ``pages/about/team/template.djx``, visible components are: root components, ``pages/_components``, and ``pages/about/_components``. Components under e.g. ``pages/blog/_components`` are not visible there.

Template syntax
---------------

**Invoking a component**

- Without body: ``{% component "card" title="Post 1" description="First post" %} {% endcomponent %}``
- With slots: put ``{% slot "name" %} ... {% endslot %}`` inside the component block; the component template can render them with ``{% set_slot "name" %} ... {% endset_slot %}`` (with optional default content between the tags).

Components are available in ``template.djx`` and ``layout.djx`` without a ``{% load %}`` (they are in builtins). Use the same block form (with ``{% endcomponent %}``) even when there is no body.

**Defining slots in the component template**

- ``{% set_slot "avatar" %}`` … ``{% endset_slot %}`` — place where slot content is inserted; the content between the tags is the default if the slot is not provided.

Example (call site):

.. code-block:: html

   {% component "profile" username="Admin" %}
     {% slot "avatar" %}
       <img src="/avatar.png" alt="Avatar" />
     {% endslot %}
   {% endcomponent %}

Example (component template ``_components/profile/component.djx``):

.. code-block:: html

   <div class="profile">
     <div class="avatar">
       {% set_slot "avatar" %}
         <span class="badge">{{ username.0 }}</span>
       {% endset_slot %}
     </div>
     <div class="info">{{ username }}</div>
   </div>

.. _components-checks:

Checks
------

- **Duplicate component names** — Within the same scope, no two components may share the same name. ``manage.py check`` reports an error (e.g. ``next.E020``) with the conflicting paths.
- **No page context in component.py** — ``component.py`` must not use context from ``next.pages``. Use component context from ``next.components`` only. Reported by check (e.g. ``next.E021``).

Example project
----------------

A full example with simple and composite components, root components, and scope is in the repository under ``examples/components/`` (see its ``README.md`` and ``tests.py``).
