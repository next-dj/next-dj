.. _topics-project-layout:

Project layout
==============

This page covers the directory layout that next.dj expects for a single Django project.
It assumes one Django application that ships pages, components, and form actions.
For projects with several applications or a shared UI kit, read :doc:`multi-project`.

.. contents::
   :local:
   :depth: 2

Recommended tree
----------------

The Notes project from the tutorial demonstrates the full layout.

.. code-block:: text
   :caption: full project tree

   notes_project/
     manage.py
     pyproject.toml
     pytest.ini
     conftest.py
     config/
       __init__.py
       settings.py
       urls.py
       wsgi.py
       asgi.py
     notes/
       __init__.py
       apps.py
       models.py
       forms.py
       admin.py
       migrations/
       pages/
         layout.djx
         layout.css
         page.py
         template.djx
         template.css
         _components/
           note_card/
             component.djx
             component.py
             component.css
             component.js
         notes/
           layout.djx
           [id]/
             page.py
             template.djx
             edit/
               page.py
               template.djx
     static/
       favicon.ico
     tests/
       __init__.py
       conftest.py
       test_e2e.py

Three things are special about this tree.

- ``pages/`` is the page root. Every directory below it becomes a URL.
- ``_components/`` lives inside the page root. Every directory below it becomes a reusable component.
- ``static/`` keeps project-wide assets that are not co-located with a page or a component.

The file router registers each ``_components/`` folder it meets while walking the page tree, and it skips that folder as a route.
A ``_components/`` folder placed beside ``pages/`` rather than inside it is never walked, so the ``{% component %}`` tag finds nothing there.
A component root outside the page tree needs an explicit ``DIRS`` entry on the component backend, covered in :doc:`/content/howto/share-components-across-projects`.

Configuration touchpoints
-------------------------

Two ``NEXT_FRAMEWORK`` keys point at the directories above.
The project ``static/`` directory is reached through Django ``STATICFILES_DIRS`` instead, see *Static files* below.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [],
               "APP_DIRS": True,
               "PAGES_DIR": "pages",
               "OPTIONS": {"context_processors": []},
           }
       ],
       "COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [],
               "COMPONENTS_DIR": "_components",
           }
       ],
   }

``PAGES_DIR`` names the page root inside each application.
``COMPONENTS_DIR`` names the component folder the router looks for inside that page root.
The names are convention.
You can choose anything that fits your domain.

Settings helpers
----------------

When you need to override a single key inside ``PAGE_BACKENDS`` without rewriting the entire list, use ``extend_default_backend``.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": extend_default_backend(
           "PAGE_BACKENDS",
           PAGES_DIR="screens",
       )
   }

The helper deep copies the default list and patches the first backend entry.
Scalar and list overrides replace the existing value.
Dict overrides such as ``OPTIONS`` are merged one level deep into the default dict.
Use it for narrow overrides such as changing the page directory name.

A dict override illustrates the one-level-deep merge.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": extend_default_backend(
           "PAGE_BACKENDS",
           OPTIONS={"context_processors": ["myapp.context.site"]},
       )
   }

The default backend ships ``OPTIONS`` carrying only the ``context_processors`` key.
The override merges into that dict one level deep, so any key the override omits keeps its default value while the supplied ``context_processors`` list replaces the empty default.

Per project page DIRS
---------------------

A project that hosts a global layout or a project-wide page tree adds an entry to ``DIRS``.

.. code-block:: python
   :caption: config/settings.py

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [str(BASE_DIR / "chrome")],
               "APP_DIRS": True,
               "PAGES_DIR": "pages",
               "OPTIONS": {"context_processors": []},
           }
       ]
   }

The ``chrome`` directory holds a top level ``layout.djx`` plus optional project pages.
The router walks the application directories first, then enters ``chrome``.
The chrome layout wraps every application page.

Tests
-----

Place tests under ``tests/`` at the project root.
The root ``conftest.py`` holds pytest collection settings, while ``tests/conftest.py`` activates registry isolation through ``reset_registries`` (see :doc:`/content/topics/testing`).

A per application ``tests/`` directory works for projects with several applications.
See :doc:`multi-project` for the layered layout.

Static files
------------

Project-wide assets that are not owned by a page or a component live under ``static/``.
The static finders include co-located assets, the project ``static/`` directory, and every entry in ``STATICFILES_DIRS``.

Migrations
----------

next.dj does not touch migrations.
Run ``uv run python manage.py makemigrations`` and ``uv run python manage.py migrate`` exactly as in a regular Django project.

Custom management commands
--------------------------

Place commands inside ``notes/management/commands/``.
The framework does not require any special wiring beyond what Django already documents.

Common variations
-----------------

Single application mode
~~~~~~~~~~~~~~~~~~~~~~~

A small project lives entirely inside one application.
The tree above is the typical shape.

Project layout with chrome
~~~~~~~~~~~~~~~~~~~~~~~~~~

Add a ``chrome/`` directory at the project root and reference it through ``DIRS``.
The chrome holds a project-wide layout and possibly a few project-level pages such as ``/login`` or ``/health``.

Per domain trees
~~~~~~~~~~~~~~~~

Define two backends in ``PAGE_BACKENDS``.
Each backend walks a different directory.
The first matching URL pattern wins, so the order matters.

See also
--------

.. seealso::

   :doc:`file-router` for the route shapes.
   :doc:`multi-project` for layouts that span several applications.
   :doc:`/content/howto/share-components-across-projects` for shared component patterns.
