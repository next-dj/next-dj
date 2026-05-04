File Router
===========

next.dj provides a powerful file-based routing system that automatically generates Django URL patterns from your file system structure.
This eliminates the need to manually write URL configurations.

How It Works
------------

The file router scans your project for ``page.py`` files and automatically creates Django URL patterns based on the directory structure.
Each directory becomes a URL segment, creating a hierarchical URL structure that mirrors your file system.

Basic File Structure
~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   pages/
   ├── home/
   │   └── page.py          -> /home/
   ├── about/
   │   └── page.py          -> /about/
   └── blog/
       ├── page.py          -> /blog/
       └── post/
           └── [slug]/
               └── page.py  -> /blog/post/<str:slug>/

URL Pattern Generation
----------------------

The system automatically converts file paths to Django URL patterns using a custom syntax:

**String Parameters** (default):

.. code-block:: text

   [username]               -> <str:username>

**Typed Parameters**:

.. code-block:: text

   [int:post_id]           -> <int:post_id>
   [slug:category]         -> <slug:category>
   [uuid:user_id]          -> <uuid:user_id>

**Wildcard Parameters**:

.. code-block:: text

   [[path]]                -> <path:path>

**Examples**:

::

   pages/
   ├── user/
   │   └── [username]/
   │       └── page.py      -> /user/<str:username>/
   ├── post/
   │   └── [int:post_id]/
   │       └── page.py      -> /post/<int:post_id>/
   └── api/
       └── [[path]]/
           └── page.py      -> /api/<path:path>/

Virtual Routes
--------------

Routes can exist without ``page.py`` files if they have a ``template.djx`` file:

.. code-block:: text

   pages/
   ├── static/
   │   └── about/
   │       └── template.djx  -> /static/about/ (virtual route)
   └── api/
       └── health/
           └── template.djx  -> /api/health/ (virtual route)

URL Naming
----------

URL patterns are automatically named based on the file path:

.. code-block:: text

   pages/home/page.py                    -> page_home
   pages/user/[username]/page.py         -> page_user_username
   pages/blog/post/[int:post_id]/page.py -> page_blog_post_post_id

Use these names in your templates:

.. code-block:: html

   <a href="{% url 'page_home' %}">Home</a>
   <a href="{% url 'page_user_username' username='john' %}">John's Profile</a>

Configuration
-------------

Configure the file router in your Django settings (``NEXT_FRAMEWORK``):

.. code-block:: python

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "PAGES_DIR": "pages",
               "APP_DIRS": True,  # Scan Django app directories
               "DIRS": [],
               "OPTIONS": {
                   "context_processors": [
                       "myapp.context_processors.global_context",
                   ],
               },
           },
       ],
   }

**APP_DIRS**: Whether to scan Django app directories (default: True)
**DIRS**: Extra filesystem roots for pages (path-like entries). See below.
**context_processors**: List of context processor paths for global template variables (inside ``OPTIONS``)

The folder name skipped during URL scanning (so it does not become a route segment) is ``COMPONENTS_DIR`` on ``DEFAULT_COMPONENT_BACKENDS`` (see :doc:`components`), not on each page router entry.

Root and App Pages (Like Staticfiles)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Resolution works like Django's static files:

- **Project-level directories** — List absolute or project-relative paths in ``DIRS``.
  These behave like ``STATICFILES_DIRS``.
  Each entry must exist on disk as a directory.
  Each root contains ``page.py`` / ``template.djx`` and optionally ``layout.djx`` for a global layout.
- **App directories** — With ``APP_DIRS: True``, each installed app's ``pages/`` directory is scanned (like each app's ``static/`` folder).
  The subdirectory name comes from top-level ``PAGES_DIR`` (default ``"pages"``).

You can use both in one backend: set ``APP_DIRS: True`` and add path roots to ``DIRS``.
URL patterns are then built in this order: first from app pages, then from each root directory in ``DIRS``.
If the same URL pattern is defined in both an app and a root directory, ``python manage.py check`` reports an error (``next.E015``).

.. code-block:: python

   from pathlib import Path
   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "PAGES_DIR": "pages",
               "APP_DIRS": True,
               "DIRS": [str(BASE_DIR / "root_pages")],
               "OPTIONS": {
                   "context_processors": [...],
               },
           },
       ],
   }

**DIRS**: List of extra root-level pages directories (in addition to app trees when ``APP_DIRS`` is true).

Component folder and file routing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``COMPONENTS_DIR`` value on ``DEFAULT_COMPONENT_BACKENDS`` (default ``"_components"`` in framework defaults)
sets the folder name that is **not** used for file-based routing and does not create URL segments.
The file router uses that same string when scanning for ``page.py`` and ``template.djx``.
Only the configured name is skipped (not all directories starting with an underscore).
See :doc:`components` for the components system.

Multiple Configurations
~~~~~~~~~~~~~~~~~~~~~~~

next.dj supports multiple router entries in ``NEXT_FRAMEWORK["DEFAULT_PAGE_BACKENDS"]``, allowing you to have different routing strategies for different parts of your application:

.. code-block:: python

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "PAGES_DIR": "pages",
               "APP_DIRS": True,
               "DIRS": [],
               "OPTIONS": {"context_processors": []},
           },
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "PAGES_DIR": "admin_pages",
               "APP_DIRS": True,
               "DIRS": [],
               "OPTIONS": {"context_processors": []},
           },
       ],
   }

This allows you to have:
- **Main site routes** in ``pages/`` directory
- **Admin routes** in ``admin_pages/`` directory
- **Different layout hierarchies** for different sections
- **Separate context processors** for different areas

Router Discovery
----------------

The system discovers routes by:

1. **Scanning app directories**: Each Django app's ``pages/`` directory
2. **Scanning root pages**: Project root ``pages/`` directory
3. **Processing page files**: Converting ``page.py`` files to URL patterns
4. **Processing virtual routes**: Converting ``template.djx`` files to URL patterns

Route Caching
-------------

Routes are cached for performance. The cache is cleared when:

1. Django development server restarts
2. Page files are modified
3. Template files are modified
4. Settings are changed

Validation Checks
-----------------

The system includes comprehensive validation checks to prevent configuration errors:

**Configuration Validation** (``check_next_pages_configuration``):
- Validates ``NEXT_FRAMEWORK`` (including ``DEFAULT_PAGE_BACKENDS``) structure
- Checks for required fields (BACKEND)
- Validates backend types and option structures
- Ensures all configured backends can be instantiated

**Page Structure Validation** (``check_pages_structure``):
- Validates directory names for proper parameter syntax
- Checks for missing page.py files in parameter directories
- Validates file organization and naming conventions

**Page Function Validation** (``check_page_functions``):
- Ensures page.py files have valid render functions or templates
- Warns on empty pages when no template, render, or djx files are present

**URL Pattern Validation** (``check_url_patterns``):
- Generates URL patterns and validates them for conflicts
- Checks for duplicate URL names and parameter consistency
- Identifies potential routing conflicts

**Context Function Validation** (``check_context_functions``):
- Validates that context functions return proper data types
- Ensures @context decorated functions return dictionaries when used without keys

Run validation checks:

.. code-block:: bash

   python manage.py check

Examples
--------

See the ``examples/`` directory in the source repository for complete
working projects:

- ``examples/_template`` — minimal scaffold (routes, layout, template,
  widget component).
- ``examples/shortener`` — URL shortener with bracket segments
  (``[slug]``) and nested ``admin/`` routes.
- ``examples/markdown-blog`` — per-post pages under ``posts/<slug>/``
  with a custom ``TemplateLoader``.
- ``examples/feature-flags`` — admin panel with nested layouts and
  form actions.

Best Practices
--------------

1. **Use meaningful directory names**: They become URL segments
2. **Keep routes shallow**: Avoid deep nesting when possible
3. **Use parameters wisely**: Choose appropriate parameter types
4. **Test all routes**: Ensure they work correctly
5. **Follow naming conventions**: Use consistent directory and file names
6. **Handle errors gracefully**: Always provide fallback data in context functions

Extension points
----------------

The URL routing subsystem exposes three pluggable surfaces.

* ``next.urls.backends.RouterBackend`` is the abstract contract for generating URL patterns. Subclass it to serve routes from a source other than the filesystem.
* ``next.urls.backends.FileRouterBackend`` is the default implementation. Subclass it to keep the filesystem walk but augment the generated patterns.
* ``next.urls.backends.RouterFactory.register_backend`` lets you map a custom dotted path to a backend class without editing the factory itself.

Register a custom backend through the settings contract.

.. code-block:: python

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "myapp.custom_router.TaggedFileRouterBackend",
               "PAGES_DIR": "pages",
               "APP_DIRS": True,
           },
       ],
   }

The signals emitted by :mod:`next.urls.signals` let external code observe routing decisions without subclassing.

* ``route_registered`` fires when a backend yields a new URL pattern.
* ``router_reloaded`` fires after the ``RouterManager`` rebuilds its pattern list. The ``sender`` is the ``RouterManager`` class.

A worked inline ``TaggedFileRouterBackend`` example is in :doc:`extending` (section "Worked examples by subsystem").

Reloading the router after data changes
---------------------------------------

A router backend that reads URLs from a database has to rebuild its pattern list whenever the underlying rows change. The ``RouterManager`` exposes a public ``reload`` method for that case.

.. code-block:: python

   from django.db.models.signals import post_delete, post_save
   from django.dispatch import receiver

   from next.urls import router_manager

   from .models import Article


   @receiver(post_save, sender=Article)
   @receiver(post_delete, sender=Article)
   def reload_router_on_article_change(**_kwargs) -> None:
       """Rebuild URL patterns whenever an article appears or disappears."""
       router_manager.reload()

The call is idempotent. Each invocation rebuilds the backend list from
``DEFAULT_PAGE_BACKENDS``, clears the Django URL resolver cache, and emits one
``router_reloaded`` event. The next request observes the new patterns without a
process restart.

The same path runs automatically when ``next_framework_settings.reload`` fires
after a settings change. Application code rarely needs to call that broader
entry point. Prefer ``router_manager.reload`` when only the URL surface needs
to refresh.

Next
----

:doc:`pages-and-templates` — Pages, DJX templates, and layout inheritance.
