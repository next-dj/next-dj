.. _topics-file-router:

File Router
===========

The file router scans your project for ``page.py`` and ``template.djx`` files and generates Django URL patterns from the directory tree.
This page covers every route shape the router accepts, how URL parameters are captured and typed, how URL names are computed, and how to mount several routers side by side.

.. contents::
   :local:
   :depth: 2

Overview
--------

A directory under a configured page root becomes a URL segment.
A directory with a ``page.py`` becomes a navigable URL.
A directory with only a ``template.djx`` becomes a virtual route that renders static markup.
A bracketed segment such as ``[slug]`` is captured as a URL parameter and exposed to the page through the :doc:`dependency resolver <dependency-injection>`.

The router does not need an entry in ``urls.py`` per page.
Adding a directory adds a URL.
Renaming a directory renames the URL and its computed URL name.
Removing a directory removes the URL.

Route Shapes
------------

The router recognises four directory shapes.

Plain segment.
   A directory name without brackets becomes a static URL segment.
   ``routes/blog/page.py`` answers ``/blog/``.

Captured segment.
   A directory wrapped in single brackets becomes a captured URL parameter.
   ``routes/posts/[slug]/page.py`` answers ``/posts/<str:slug>/``.

Typed captured segment.
   A captured directory with a converter prefix sets the Django path converter.
   ``routes/posts/[int:post_id]/page.py`` answers ``/posts/<int:post_id>/``.

Wildcard segment.
   A directory wrapped in double brackets becomes a ``path`` converter that swallows multiple URL segments.
   ``routes/api/[[suffix]]/page.py`` answers ``/api/<path:suffix>/``.

The following layout shows the four shapes together.

.. code-block:: text
   :caption: routes tree

   routes/
     page.py                       /
     blog/
       page.py                     /blog/
     posts/
       [slug]/
         page.py                   /posts/<str:slug>/
       [int:post_id]/
         page.py                   /posts/<int:post_id>/
     api/
       [[suffix]]/
         page.py                   /api/<path:suffix>/

Captured Parameters
-------------------

The bracket syntax accepts every Django path converter.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Bracket
     - Generated converter
     - When to use
   * - ``[name]``
     - ``<str:name>``
     - Any non empty value with no slash.
   * - ``[int:name]``
     - ``<int:name>``
     - Positive integers, including zero.
   * - ``[slug:name]``
     - ``<slug:name>``
     - URL slugs of ASCII letters, digits, hyphens, and underscores.
   * - ``[uuid:name]``
     - ``<uuid:name>``
     - Canonical UUID strings.
   * - ``[[name]]``
     - ``<path:name>``
     - Wildcard that matches one or more segments including slashes.

A bracket label is passed to Django verbatim, so any converter registered with :func:`django.urls.register_converter` works in ``[label:name]``.
The parser handles three bracket forms.
The typed captured segment is the captured form with a converter prefix, as covered in :doc:`/content/internals/url-router`.

The ``[[name]]`` wildcard requires at least one character.
A request to the parent path with no trailing segment, such as ``/api/`` for an ``api/[[suffix]]/`` route, does not match, because the Django ``path`` converter never captures an empty string.

Captured values reach Python through markers.
``DUrl[T]`` parses the captured value into the requested type and provides it to context functions and action handlers.

Hyphens in directory names are normalised to underscores in the generated URL parameter and URL name.
A ``routes/[my-id]/page.py`` route becomes the Django parameter ``<str:my_id>``, the resolver provides it as ``my_id``, and the URL name registers as ``next:page_my_id``.
Name your directories without hyphens when you want the parameter name and the directory name to match exactly.

.. code-block:: python
   :caption: routes/posts/[int:post_id]/page.py

   from notes.models import Note
   from next.pages import context
   from next.urls import DUrl

   @context("note")
   def fetch_note(post_id: DUrl[int]) -> Note:
       return Note.objects.get(pk=post_id)

``DUrl[int]`` reads the captured segment whose name matches the parameter, so ``post_id`` resolves the ``[int:post_id]`` segment and the marker coerces it to ``int``.
See :doc:`dependency-injection` for the full set of ``DUrl`` forms and the coercion table.

Virtual Routes
--------------

A directory that contains a ``template.djx`` without a ``page.py`` still becomes a URL.
The router renders the template directly without invoking a Python page module.

.. code-block:: text
   :caption: routes tree

   routes/
     about/
       template.djx                /about/
     legal/
       privacy/
         template.djx              /legal/privacy/

Virtual routes are useful for marketing pages, static content, and quick mockups.
A virtual route can still receive layout wrapping from any ancestor ``layout.djx``.

URL Names
---------

Every page receives a URL name in the ``next`` namespace.
The name is computed from the URL path with the leading slash removed and each segment separated by an underscore.
Captured segments contribute their parameter name without the brackets.

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - File
     - URL name
   * - ``routes/page.py``
     - ``next:page_``
   * - ``routes/blog/page.py``
     - ``next:page_blog``
   * - ``routes/posts/[slug]/page.py``
     - ``next:page_posts_slug``
   * - ``routes/posts/[int:post_id]/page.py``
     - ``next:page_posts_int_post_id``
   * - ``routes/api/[[suffix]]/page.py``
     - ``next:page_api_suffix``

The trailing underscore on the root page name is intentional.
``reverse('next:page_')`` resolves the root page.

A typed captured segment keeps its converter label in the URL name.
``[int:post_id]`` becomes ``posts_int_post_id``, not ``posts_post_id``, because the name is computed from the raw segment text.

The ``page_`` prefix comes from the ``URL_NAME_TEMPLATE`` setting.
Its default is ``page_{name}``, where ``{name}`` is the underscore-joined path computed above.
Set it to change the prefix for every file-routed page at once.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "URL_NAME_TEMPLATE": "route_{name}",
   }

With this value ``routes/blog/page.py`` registers as ``next:route_blog``.
The placeholder ``{name}`` must appear in the template so each page still gets a distinct name.

Reverse them through the standard ``{% url %}`` tag or with ``page_reverse``.
See :doc:`url-reversing` for the Python side.

Page Roots
----------

The router resolves routes from two sources, in the same way ``staticfiles`` resolves static files.

App directories.
   When ``APP_DIRS`` is ``True`` the router scans each installed application for a directory named ``PAGES_DIR``.
   In the tutorial this is ``notes/pages/`` because ``PAGES_DIR`` defaults to ``pages``.

Project directories.
   The ``DIRS`` list adds absolute or project-relative paths to the scan.
   The router walks each directory in order and registers every ``page.py`` and ``template.djx`` it finds.

You can use both sources at once.
URL patterns are built in this order, first from application directories then from each entry in ``DIRS``.
If two routes resolve to the same Django path the system check ``next.E015`` reports the conflict, whether they come from one tree or several.

.. code-block:: python
   :caption: config/settings.py

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "DIRS": [str(BASE_DIR / "chrome")],
               "PAGES_DIR": "routes",
               "OPTIONS": {
                   "context_processors": [
                       "myapp.context_processors.global_context",
                   ],
               },
           }
       ]
   }

The ``OPTIONS`` block accepts a list of Django context processor paths.
Each processor contributes values to every template that the router renders.

DIRS Entry Types
~~~~~~~~~~~~~~~~

Each entry in ``DIRS`` is classified by ``next.utils.classify_dirs_entries`` before the router uses it.

Path entry.
   An absolute path, or a relative path that resolves to an existing directory under ``settings.BASE_DIR``.
   The router walks this directory as an additional page root alongside the application directories.

Segment entry.
   A plain string such as ``"api"`` or ``"_internal"`` that does not resolve to an existing directory.
   The router adds it to the set of directory names it skips during the file walk, preventing those directories from becoming URL segments.
   This is an alternative to the automatic ``_components`` skip that comes from ``DEFAULT_COMPONENT_BACKENDS``.

.. code-block:: python
   :caption: skipping a directory by name

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": ["_drafts"],
               "APP_DIRS": True,
               "PAGES_DIR": "routes",
               "OPTIONS": {"context_processors": []},
           }
       ]
   }

In the example above, any directory named ``_drafts`` under any application's page root is silently skipped.
No URL is registered for it and the file walk does not descend into it.

Components Folder Skipping
--------------------------

The router shares its file walk with the components backend.
The name set in the first ``DEFAULT_COMPONENT_BACKENDS`` entry under ``COMPONENTS_DIR`` becomes a directory that the router does not enter.
The default is ``_components``.
Only that exact name is skipped, not every directory that starts with an underscore.

Multiple Backends
-----------------

The settings list accepts more than one backend.
Each backend can read from a different directory, register a different ``PAGES_DIR``, or use a custom subclass.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [],
               "APP_DIRS": True,
               "PAGES_DIR": "routes",
               "OPTIONS": {"context_processors": []},
           },
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [],
               "APP_DIRS": True,
               "PAGES_DIR": "admin_routes",
               "OPTIONS": {"context_processors": []},
           }
       ]
   }

Two backends produce two independent sets of URLs.
The Django URL resolver checks them in order, the first match wins.
Both backends emit the same signals and follow the same naming rules.

Common Patterns
---------------

Single Page Application Root
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A single ``routes/page.py`` registers the empty path ``/``.
The router treats it as the default URL for the project.

Static Content Section
~~~~~~~~~~~~~~~~~~~~~~

Use virtual routes for marketing pages and legal copy.
The directory holds only a ``template.djx``, no Python required.

Per Project Page Tree
~~~~~~~~~~~~~~~~~~~~~

Place a layout and one ``page.py`` under ``chrome/`` and add ``chrome`` to ``DIRS``.
The result is a project-level shell that wraps every application page.
See :doc:`multi-project` for the full pattern.

Hot Reload
----------

A backend that reads from a database or other dynamic source needs to rebuild its pattern list when the data changes.
``router_manager.reload()`` clears the resolver cache and rebuilds every backend, and the call is idempotent.
Each invocation emits a ``router_reloaded`` signal with the manager class as sender, so long lived processes can listen for it to refresh cached URL references.
See :doc:`/content/howto/reload-routes-from-code` for the model-signal receiver that triggers the reload.

System Checks
-------------

The router contributes Django system checks that validate the configuration at startup.

- ``check_next_pages_configuration`` validates the ``NEXT_FRAMEWORK`` structure and each backend entry.
- ``check_pages_structure`` validates directory naming, captured parameter syntax, and the presence of ``page.py`` or ``template.djx``.
- ``check_page_functions`` reports :ref:`next.E012 <ref-system-checks>` when a directory has neither a render function nor a template.
- ``check_pages_structure`` and ``check_page_functions`` come from ``next.pages`` and appear here because they validate the same page tree the router scans.
- ``check_url_patterns`` reports two routes that resolve to the same Django path, whether they come from one tree or several (:ref:`next.E015 <ref-system-checks>`).
- ``check_duplicate_url_parameters`` fails when one route repeats a captured parameter name (:ref:`next.E028 <ref-system-checks>`).

Run them through ``uv run python manage.py check``.
A clean exit confirms that every page resolves and every name is unique.

Extension Points
----------------

Three surfaces let you replace or augment the router.

- ``next.urls.backends.RouterBackend`` is the abstract contract for any source of URL patterns.
- ``next.urls.backends.FileRouterBackend`` is the default file-based implementation.
- ``next.urls.backends.RouterFactory.register_backend`` maps a dotted path to a custom backend.

Subclass ``FileRouterBackend`` to add additional patterns or augment URL names without writing a backend from scratch.
See :doc:`extending` for a worked example.

Database Driven Routes
----------------------

A hybrid backend combines file routes with routes built from database rows.
Subclass ``FileRouterBackend`` and override ``generate_urls`` to call ``super().generate_urls()`` for the file routes, then append one named pattern per row.
Register the backend in ``DEFAULT_PAGE_BACKENDS`` and call ``router_manager.reload()`` from a model signal so the row-derived patterns rebuild when the data changes.

See :doc:`/content/howto/write-a-router-backend` for the full worked recipe.

See Also
--------

.. seealso::

   :doc:`url-reversing` for building URLs in Python and templates.
   :doc:`/content/howto/add-a-page` for a one page recipe.
   :doc:`/content/howto/reload-routes-from-code` for hot reload mechanics.
   :doc:`/content/internals/url-router` for the parser, dispatcher, and signal flow.
   :doc:`/content/ref/urls` for the public API.
