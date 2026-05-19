.. _topics-project-layout:

Project Layout
==============

This page covers the directory layout that next.dj expects for a single Django project.
It assumes one Django application that ships pages, components, and form actions.
For projects with several applications or a shared UI kit, read :doc:`multi-project`.

.. contents::
   :local:
   :depth: 2

Recommended Tree
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
       _components/
         note_card/
           component.djx
           component.py
           component.css
           component.js
       routes/
         layout.djx
         layout.css
         page.py
         template.djx
         template.css
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

- ``routes/`` is the page root. Every directory below it becomes a URL.
- ``_components/`` lives at the application root. Every directory below it becomes a reusable component.
- ``static/`` keeps project-wide assets that are not co-located with a page or a component.

Configuration Touchpoints
-------------------------

Three settings keys point at the directories above.

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
           }
       ],
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "COMPONENTS_DIR": "_components",
           }
       ],
   }

``PAGES_DIR`` names the page root inside each application.
``COMPONENTS_DIR`` names the component root inside each application.
The names are convention.
You can choose anything that fits your domain.

Settings Helpers
----------------

When you need to override a single key inside ``DEFAULT_PAGE_BACKENDS`` without rewriting the entire list, use ``extend_default_backend``.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": extend_default_backend(
           "DEFAULT_PAGE_BACKENDS",
           PAGES_DIR="screens",
       )
   }

The helper merges the new dict into the existing first backend entry.
Use it for narrow overrides such as changing the page directory name.

Per Project Page DIRS
---------------------

A project that hosts a global layout or a project-wide page tree adds an entry to ``DIRS``.

.. code-block:: python
   :caption: config/settings.py

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [str(BASE_DIR / "chrome")],
               "APP_DIRS": True,
               "PAGES_DIR": "routes",
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

Static Files
------------

Project-wide assets that are not owned by a page or a component live under ``static/``.
The static finders include co-located assets, the project ``static/`` directory, and every entry in ``STATICFILES_DIRS``.

Migrations
----------

next.dj does not touch migrations.
Run ``uv run python manage.py makemigrations`` and ``uv run python manage.py migrate`` exactly as in a regular Django project.

Custom Management Commands
--------------------------

Place commands inside ``notes/management/commands/``.
The framework does not require any special wiring beyond what Django already documents.

Common Variations
-----------------

Single Application Mode
~~~~~~~~~~~~~~~~~~~~~~~

A small project lives entirely inside one application.
The tree above is the typical shape.

Project Layout With Chrome
~~~~~~~~~~~~~~~~~~~~~~~~~~

Add a ``chrome/`` directory at the project root and reference it through ``DIRS``.
The chrome holds a project-wide layout and possibly a few project-level pages such as ``/login`` or ``/health``.

Per Domain Trees
~~~~~~~~~~~~~~~~

Define two backends in ``DEFAULT_PAGE_BACKENDS``.
Each backend walks a different directory.
The first matching URL pattern wins, so the order matters.

See Also
--------

.. seealso::

   :doc:`file-router` for the route shapes.
   :doc:`multi-project` for layouts that span several applications.
   :doc:`/content/howto/share-components-across-projects` for shared component patterns.
