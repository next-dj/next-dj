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

Call it from a template in scope (``template.djx``, ``layout.djx``, or another component) using the block form:

.. code-block:: django

   {% component "card" title="Hello" %}{% endcomponent %}

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

Everything after the component name on ``{% component %}`` is parsed as ``name="value"`` tokens with **static** string values. You cannot pass a template variable there (for example ``title={{ post.title }}`` is not supported). Pass dynamic page data through **slots**, nested template content that the component template renders with ``{% set_slot %}``, or through variables added by ``@context`` in ``component.py``. The ``examples/components/`` project uses slots for list-driven content (see :ref:`components-example-project`).

**Invoking a component**

- Without body: ``{% component "card" title="Post 1" description="First post" %} {% endcomponent %}``
- With slots: put ``{% slot "name" %} ... {% endslot %}`` inside the component block. The component template can render them with ``{% set_slot "name" %} ... {% endset_slot %}`` (with optional default content between the tags).

Components are available in ``template.djx`` and ``layout.djx`` without a ``{% load %}`` (they are in builtins). Use the same block form (with ``{% endcomponent %}``) even when there is no body.

**Defining slots in the component template**

- ``{% set_slot "avatar" %}`` … ``{% endset_slot %}`` — place where slot content is inserted. The content between the tags is the default if the slot is not provided.

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

The ``examples/components/`` project shows a realistic setup: composite **header** with ``@context("user")`` and navigation, **footer** as a simple ``.djx`` file, **post cards** with slots inside a loop, and root versus branch-scoped ``_components``. See its ``README.md`` and ``tests.py`` in the repository.
