.. _howto-share-components-across-projects:

Share Components Across Projects
================================

Problem
-------

Several Django projects in the same repository should reuse a single UI kit instead of duplicating component code.

Scope
-----

:doc:`/content/topics/multi-project` is the full guide and covers page ``DIRS``, shared static, autoreload, and naming conventions.
This page is the shortest path and points ``COMPONENT_BACKENDS`` at one shared folder.

Solution
--------

Place the shared components in one folder at the repository level and reference it from each project through ``COMPONENT_BACKENDS["DIRS"]``.

Walkthrough
-----------

Set up the repository layout.

.. code-block:: text
   :caption: repo

   repo/
     _shared/
       _components/
         button/
           component.djx
           component.py
         card/
           component.djx
     projects/
       admin/
         config/
           settings.py
       site/
         config/
           settings.py

Add the shared directory to each project.

.. code-block:: python
   :caption: projects/admin/config/settings.py

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent
   SHARED_DIR = BASE_DIR.parent.parent / "_shared"

   NEXT_FRAMEWORK = {
       "COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [str(SHARED_DIR / "_components")],
               "COMPONENTS_DIR": "_components",
           }
       ]
   }

Repeat the same block in ``projects/site/config/settings.py``.
Each project now sees ``button``, ``card``, and every other component in the shared folder.

Use the Components
~~~~~~~~~~~~~~~~~~

.. code-block:: jinja
   :caption: any project template

   {% component "button" text="Save" variant="primary" %}

The framework resolves the component by name through the component visibility resolver.

Per Project Overrides
~~~~~~~~~~~~~~~~~~~~~

A project can override a shared component by placing a component with the same name in its own components root.

.. code-block:: text
   :caption: project override

   projects/admin/admin_app/_components/button/component.djx

The project local version wins because the visibility resolver scores the project's page-tree root and a global ``DIRS`` root equally and breaks the tie by registration order.
The page-tree backend registers first during the URL router walk, so its components shadow same-name entries contributed through ``DIRS``.

Static Files
~~~~~~~~~~~~

Add the shared static directory to ``STATICFILES_DIRS`` if the components ship CSS or images outside the co-located stems.

.. code-block:: python
   :caption: projects/admin/config/settings.py

   STATICFILES_DIRS = [
       BASE_DIR / "static",
       SHARED_DIR / "static",
   ]

Verification
------------

Run each project independently with ``uv run python manage.py runserver`` from inside the project directory.
Confirm that the shared component renders in both projects.

See Also
--------

.. seealso::

   :doc:`/content/topics/multi-project` for the full multi-project layout and hot reload.
   :doc:`/content/topics/components` for the component lifecycle.
