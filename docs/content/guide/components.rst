Components
==========

Components let you reuse template fragments (cards, headers, profiles) with props and slots. They live next to your pages in a dedicated folder and are resolved by **scope**: a template only sees components from its branch and from root-level component directories.

.. _components-routing:

Component folder and file routing
---------------------------------

The directory name used for components (e.g. ``_components``) is set on **``DEFAULT_COMPONENT_BACKENDS``** as ``COMPONENTS_DIR``. The file router always uses that value when building URL scans so the folder is skipped and does **not** become a route segment. Only this configured name is skipped (not every directory that starts with an underscore). See :doc:`file-router` for the routing side.

Backends and settings
---------------------

Components are provided by backends, similar to the page router. In Django settings, use the top-level dict ``NEXT_FRAMEWORK`` with the key **``DEFAULT_COMPONENT_BACKENDS``**: a list of backend configs. Each item is a dict that is passed unchanged into the backend class constructor:

- ``BACKEND`` (str) — dotted import path of the backend class (default for the built-in file backend is ``"next.components.FileComponentsBackend"``).
- ``COMPONENTS_DIR`` (str, default ``"_components"`` in framework defaults) — folder name under each page tree where components are discovered and the name the file router skips during URL discovery. The file router reads this value from the first component backend entry.
- ``DIRS`` (list) — extra filesystem directories registered as **global** component roots (visible from every template). Entries are split into real paths and segment names the same way as page ``DIRS``. Only existing directory paths are used. App and root page trees do not need to be listed here because the same walk that builds URL patterns registers component folders there.
- You may list **several backends** in ``DEFAULT_COMPONENT_BACKENDS``. Earlier entries win when the same component name appears twice.

``component.py`` modules are always loaded with the framework’s built-in :class:`~next.components.ModuleLoader`.

Lazy module loading
~~~~~~~~~~~~~~~~~~~

By default ``FileComponentsBackend`` eagerly imports every discovered
``component.py`` on startup so that decorators (``@context``, ``@action``) have
registered their side effects before the first request. Large projects with
hundreds of components may prefer to pay that cost only when a component is
first resolved:

.. code-block:: python

   NEXT_FRAMEWORK = {
       "LAZY_COMPONENT_MODULES": True,
   }

With lazy loading enabled, ``component.py`` is imported on the first call to
``get_component(name, template_path)`` for that component and cached for the
rest of the process lifetime. The filesystem scan (template discovery, scope
tree) still runs at startup — only Python module execution is deferred.
Actions or context processors defined in a deferred ``component.py`` are
therefore **not** registered until that component renders at least once, which
may matter for ``{% form %}`` tags that reference such actions on a page that
does not include the component itself.

Minimal example:

.. code-block:: python

   from pathlib import Path
   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [str(BASE_DIR / "root_components")],
               "COMPONENTS_DIR": "_components",
           },
       ],
   }

Full example with every key shown (values are illustrative. Remove or adjust what you do not need):

.. code-block:: python

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [
                   str(BASE_DIR / "root_components"),
               ],
               "COMPONENTS_DIR": "_components",
           },
           # {
           #     "BACKEND": "myapp.backends.MyComponentsBackend",
           #     "DIRS": [],
           #     "COMPONENTS_DIR": "_components",
           # },
       ],
   }

Custom backends are plain classes referenced by dotted path in ``BACKEND``. Each backend receives the **full** config dict for that list entry and reads what it needs.

Several roots in ``DEFAULT_PAGE_BACKENDS`` ``DIRS``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You may list more than one filesystem root under ``NEXT_FRAMEWORK`` ``DEFAULT_PAGE_BACKENDS`` ``DIRS``. Each entry that resolves to an existing directory is a separate page tree and is walked the same way as a single root setup. Set ``COMPONENTS_DIR`` on ``DEFAULT_COMPONENT_BACKENDS``. The file router uses that name when scanning every page tree.

Components that live in ``<root>/<COMPONENTS_DIR>/`` at the top of a tree use the empty route scope for name resolution. That level is one shared namespace across all those roots. You must not reuse the same component name in the root component folder of two different roots. Running ``python manage.py check`` surfaces that situation as ``next.E034`` and lists the paths that collide.

Using the same component name at the empty route scope and again under a nested route scope within one tree is also invalid. ``manage.py check`` reports that case as ``next.E020``. Development autoreload still registers a separate ``component.py`` glob for each root together with ``COMPONENTS_DIR`` so file changes continue to restart the runserver from every tree.

Example with two roots and a short components folder name:

.. code-block:: python

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "PAGES_DIR": "pages",
               "APP_DIRS": False,
               "DIRS": [
                   str(BASE_DIR / "custom"),
                   str(BASE_DIR / "pages"),
               ],
               "OPTIONS": {},
           },
       ],
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [],
               "COMPONENTS_DIR": "_",
           },
       ],
   }

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

Practical walkthroughs
----------------------

Minimal simple component
~~~~~~~~~~~~~~~~~~~~~~~~

Put a single file next to your pages tree, for example ``myapp/pages/_components/card.djx``:

.. code-block:: django

   <article class="card">{{ title }}</article>

Call it from a template in scope (``template.djx``, ``layout.djx``, or another component) using the **void** form (no closing tag):

.. code-block:: django

   {% component "card" title="Hello" %}

The first argument is the component name (file stem). Remaining bits on the opening tag are static ``key="value"`` props (see :ref:`components-props-literals`).

Composite with ``component.py`` and ``@context`` only
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Folder ``myapp/pages/_components/profile/`` with ``component.djx`` (for example the profile markup in :ref:`components-template-syntax`) and ``component.py`` that only registers context (no ``render``):

.. code-block:: python

   from next.components import context

   @context("username")
   def username():
       return "Admin"

The template can use ``{{ username }}`` together with slot output. Parameters of context functions are filled by the same dependency injection system as pages (see :doc:`dependency-injection`).

Composite with ``render()`` in ``component.py``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If ``component.py`` defines a callable ``render``, it runs instead of rendering ``component.djx`` for that composite. The framework resolves ``render`` parameters via dependency injection. The return value may be a ``str`` or an ``HttpResponse`` (response body is decoded to text). If there is no ``render``, the loader uses the ``component`` string attribute on the module, if set, otherwise ``component.djx``.

.. code-block:: python

   from django.http import HttpRequest

   def render(request: HttpRequest) -> str:
       return f'<p class="greeting">Hello, {request.method}</p>'

Template string only in ``component.py``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can ship a composite with **no** ``component.djx`` file by assigning a template string to ``component`` in ``component.py`` (same idea as a page ``template`` attribute). The string is rendered like a normal Django template with the merged props, slots, and context.

.. code-block:: python

   component = "<p>{{ message }}</p>"

Component context (no page context)
-----------------------------------

Context for a component is provided only through the **components** API (``next.components``), not through ``next.pages``. It is not inherited from the page by default. It only adds variables when the component is rendered.

In ``component.py`` you **must not** use context from ``next.pages`` (e.g. ``from next.pages import context`` or ``page.context``). Use the component context API from ``next.components`` instead. This is enforced by the ``python manage.py check`` system (see :ref:`components-checks`).

**Registering component context**

Use the ``@context`` decorator from ``next.components`` in your ``component.py``:

.. code-block:: python

   from django.http import HttpRequest
   from next.components import context

   @context("user")
   def get_user(request: HttpRequest):
       return request.user

   @context
   def get_data(request: HttpRequest):
       return {"count": 42, "items": [...]}

The decorator automatically detects the component file (no need to pass ``__file__``). Functions with a key (e.g. ``@context("user")``) add that key to the template context. Functions without a key must return a dictionary that gets merged into the context.

This API is similar to ``next.pages.context`` but designed specifically for components. The framework uses dependency injection to resolve function parameters (``request``, ``form``, URL kwargs, etc.).

**Exposing component context to JavaScript**

Pass ``serialize=True`` to also expose the value through ``window.Next.context``:

.. code-block:: python

   from next.components import context

   @context("theme", serialize=True)
   def get_theme() -> str:
       return "dark"

The same flag works on both import styles — ``from next.components import context``
and ``from next.components import component`` followed by ``@component.context(...)``
— because the two names refer to the same object.

.. code-block:: javascript

   // component.js or any {% #use_script %} body
   const theme = window.Next.context.theme;  // "dark"

See :ref:`next-object` in the static-assets guide for the full injection
mechanism, conflict resolution rules, and TypeScript declarations.

Scope
-----

- **Root components** (from extra ``DIRS`` roots and the ``COMPONENTS_DIR`` name under app pages) are visible from every template.
- **App/local components** (from ``_components`` under a pages directory) are visible only to templates under that directory (that page and its nested routes). Sibling branches do not see each other’s local components.

So for a template at ``pages/about/team/template.djx``, visible components are: root components, ``pages/_components``, and ``pages/about/_components``. Components under e.g. ``pages/blog/_components`` are not visible there.

.. _components-template-syntax:

Template syntax
---------------

.. _components-props-literals:

Props are literal strings
~~~~~~~~~~~~~~~~~~~~~~~~~

Everything after the component name on ``{% component %}`` / ``{% #component %}`` is parsed as ``name="value"`` tokens with **static** string values. You cannot pass a template variable there (for example ``title={{ post.title }}`` is not supported). Pass dynamic page data through **slots**, nested template content that the component template renders with ``{% #set_slot %}``, or through variables added by ``@context`` in ``component.py``. The ``examples/shortener/shortener/routes/_widgets/link_card/`` component uses slots for list-driven content (see :ref:`components-example-project`).

**Invoking a component**

- **Void** (no inner markup): ``{% component "card" title="Post 1" description="First post" %}`` — the line ends the tag; there is **no** closing tag.
- **Block** (slots or nested components): open with ``{% #component "name" ... %}`` and close with ``{% /component %}``. Inside, use:

  - ``{% #slot "name" %}`` … ``{% /slot %}`` for slot bodies with markup, or
  - ``{% slot "name" %}`` as a **short** form for an **empty** slot (name only).

- In the component template, define insertion points with ``{% #set_slot "name" %}`` … ``{% /set_slot %}``. Content between the tags is the default when the caller does not pass that slot. If there is **no** default body, use the void form ``{% set_slot "name" %}`` (same idea as short ``{% slot "name" %}`` at the call site).

Components are available in ``template.djx`` and ``layout.djx`` without a ``{% load %}`` (they are in builtins).

**Nesting**

You may nest void and block components without a fixed depth limit. Resolution uses ``current_template_path`` from the page (and is forwarded while rendering inner components) so **scope** matches the page tree (see Scope above).

Example (call site):

.. code-block:: html

   {% component "profile" username="Admin" %}
   <div class="card-list">
     {% #component "card" title="Post 1" description="First" %}
       {% #slot "image" %}
         <img src="/x.png" alt="" />
       {% /slot %}
       {% slot "footer" %}
     {% /component %}
   </div>

Example (component template ``_components/profile/component.djx``):

.. code-block:: html

   <div class="profile">
     <div class="avatar">
       {% #set_slot "avatar" %}
         <span class="badge">{{ username.0 }}</span>
       {% /set_slot %}
     </div>
     <div class="info">{{ username }}</div>
   </div>

**Template inheritance and includes**

``component.djx`` and inline ``component = "..."`` strings are normal Django templates. You may use ``{% extends %}`` or ``{% include %}`` like anywhere else. Keep component paths and block names easy to reason about because errors surface at render time for that component only.

Python API
----------

For tests, tooling, or custom code you can call the same stack the template tags use. Public helpers live in ``next.components`` (see :ref:`api-reference`): ``get_component(name, template_path)``, ``render_component(info, context_data, request=None)``, ``load_component_template(info)``, and the shared ``components_manager`` singleton for the configured backends and render pipeline. ``template_path`` should be the path of the template that is currently rendering (the same idea as ``current_template_path`` in template context).

.. _components-checks:

Checks
------

- **Duplicate component names** — Within one page tree the same logical name cannot be registered twice at overlapping route scopes. Running ``manage.py check`` emits ``next.E020`` with the paths involved.
- **Duplicate names across page roots** — The same name cannot appear in the root ``COMPONENTS_DIR`` of two different ``DIRS`` roots. ``manage.py check`` emits ``next.E034`` with each conflicting tree and path.
- **No page context in component.py** — ``component.py`` must not use context from ``next.pages``. Use component context from ``next.components`` only. Reported by check (e.g. ``next.E021``).

.. _components-example-project:

Example project
----------------

The ``examples/shortener/shortener/routes/_widgets/`` and
``examples/feature-flags/flags/panels/_chunks/`` directories show realistic
setups: a ``link_card`` component with ``@context``, a reusable ``nav_link``
component with active-state detection, and a ``feature_guard`` component
that hides content based on a flag lookup. See the per-example
``README.md`` and the ``tests/`` folders.

Extension points
----------------

The components subsystem exposes four pluggable surfaces.

* ``next.components.backends.ComponentsBackend`` is the abstract contract for sourcing component metadata. Subclass it to serve components from something other than the filesystem.
* ``next.components.backends.FileComponentsBackend`` is the default implementation. Subclass it to keep the filesystem scan and add bookkeeping.
* ``next.components.renderers.ComponentRenderStrategy`` is the protocol for swapping out how a single component is rendered. Register alternative strategies via ``ComponentsManager(strategies=...)``.
* ``next.components.backends.ComponentsFactory`` reads ``DEFAULT_COMPONENT_BACKENDS`` and imports each ``BACKEND`` dotted path.

Register a custom backend through the settings contract.

.. code-block:: python

   NEXT_FRAMEWORK = {
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "myapp.custom_backend.CountingFileComponentsBackend",
               "DIRS": [],
               "COMPONENTS_DIR": "_components",
           },
       ],
   }

The signals emitted by :mod:`next.components.signals` let external code observe component activity.

* ``component_registered`` fires when a backend reports a new component.
* ``component_backend_loaded`` fires after ``ComponentsFactory`` instantiates a backend.
* ``component_rendered`` fires after a component finishes rendering.

An inline ``CountingFileComponentsBackend`` snippet is in :doc:`extending` (section "Worked examples by subsystem").
