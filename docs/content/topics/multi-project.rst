.. _topics-multi-project:

Multi-Project Setup
===================

A multi project setup hosts several Django projects from one repository.
A shared UI kit lives in one place, each project pulls components from it, and each project keeps its own page tree.
This page covers the directory shape, the ``DIRS`` configuration, the shared components convention, and the autoreload watchers that keep development fast.

.. contents::
   :local:
   :depth: 2

When to Use Multi Project Layout
--------------------------------

Use this layout when more than one project needs to share components, layouts, and static assets without duplicating code.
The examples repository uses this shape so that every example project pulls components from ``examples/_shared/``.

For recipes focused on components only, see :doc:`/content/howto/share-components-across-projects`.

Reach for the single project layout in :doc:`project-layout` when only one Django project lives in the repository.

Directory Shape
---------------

A typical multi project repository looks like this.

.. code-block:: text
   :caption: multi project tree

   repo/
     _shared/
       _components/
         button/
           component.djx
           component.py
         card/
           component.djx
         alert/
           component.djx
       static/
         vendor.css
     projects/
       admin/
         config/
           settings.py
         chrome/
           layout.djx
         admin_app/
           routes/
             page.py
             template.djx
       site/
         config/
           settings.py
         chrome/
           layout.djx
         site_app/
           routes/
             page.py
             template.djx

Two pieces stand out.

- ``_shared/`` holds components and static files that are common to every project.
- Each project under ``projects/`` has its own ``config/`` and its own ``chrome/`` directory.

DIRS Configuration
------------------

Each project points at the shared directory through ``DIRS``.

.. code-block:: python
   :caption: projects/admin/config/settings.py

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent
   REPO_DIR = BASE_DIR.parent.parent
   SHARED_DIR = REPO_DIR / "_shared"

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [str(BASE_DIR / "chrome")],
               "APP_DIRS": True,
               "PAGES_DIR": "routes",
               "OPTIONS": {"context_processors": []},
           }
       ],
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [str(SHARED_DIR / "_components")],
               "COMPONENTS_DIR": "_components",
           }
       ],
   }

The page backend reads ``chrome/layout.djx`` from inside the project, plus every application directory.
The component backend reads from the shared components folder.

Each project picks its own ``COMPONENTS_DIR`` and its own ``PAGES_DIR``.
Different projects can use different names without affecting one another.

Static Files
------------

The shared directory ships static files alongside components.

.. code-block:: python
   :caption: projects/admin/config/settings.py

   STATICFILES_DIRS = [
       BASE_DIR / "static",
       SHARED_DIR / "static",
   ]

The Django static files finder picks up files from both directories.
Co-located assets from shared components participate through the components backend.

Shared Components Convention
----------------------------

Shared components live inside ``_shared/_components/``.
The framework reads the path from ``DIRS`` and treats it as a components root regardless of the ``COMPONENTS_DIR`` name in settings, so the on-disk directory name and the ``COMPONENTS_DIR`` setting are independent.

The shared components folder ships UI primitives such as buttons, cards, dialogs, and form widgets.
Each project consumes them through ``{% component "name" %}`` without redeclaring anything.

Per Project Components
----------------------

A project can ship project-specific components alongside the shared kit.

.. code-block:: python
   :caption: projects/admin/config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [
                   str(BASE_DIR / "chrome" / "_components"),
                   str(SHARED_DIR / "_components"),
               ],
               "COMPONENTS_DIR": "_components",
           }
       ]
   }

The list order matters.
Earlier entries win when the same component name appears twice, so a project ``DIRS`` entry placed before the shared entry silently shadows the shared component of the same name.

Hot Reload
----------

Every directory listed in a component backend ``DIRS``, including the shared ``_shared/_components/`` root, contributes its own ``**/component.py`` watch spec to the :doc:`autoreloader </content/internals/autoreload>`.
A change inside ``_shared/_components/`` fires the autoreload pipeline across every project that uses the development server.

The ``components_registered`` signal includes the full set after each reload so long-lived processes can refresh their caches.

Common Variations
-----------------

Repository Wide Layout
~~~~~~~~~~~~~~~~~~~~~~

Put a single layout in ``_shared/chrome/`` and add the path to every project's ``DIRS``.
Every project then renders inside the same shell.

Per Tenant Project
~~~~~~~~~~~~~~~~~~

Run one project per tenant from the same repository.
Each project ships its own settings, chrome, and applications.
The shared components folder keeps the design consistent across tenants.

Domain Specific Components
~~~~~~~~~~~~~~~~~~~~~~~~~~

Add a ``_internal/`` shared folder for components that should only be visible to a subset of projects.
Reference it from the appropriate projects through ``DIRS``.

See Also
--------

.. seealso::

   :doc:`project-layout` for the single project layout.
   :doc:`file-router` for the URL routing rules across roots.
   :doc:`components` for the components subsystem.
   :doc:`/content/howto/share-components-across-projects` for a recipe.
   :doc:`/content/internals/autoreload` for the watch spec pipeline.
