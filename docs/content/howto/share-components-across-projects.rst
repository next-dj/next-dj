.. _howto-share-components-across-projects:

Share components across projects
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

``COMPONENTS_DIR`` names the folder the URL router treats as a component namespace and skips instead of turning into a URL segment.
The key is required in every ``COMPONENT_BACKENDS`` entry, and ``next.E031`` reports an entry that omits it, while only the value of the first entry takes effect.
It is unrelated to ``DIRS``, which is where this recipe puts the shared kit, so the value above is the name each project uses inside its own page tree.

Every ``component.py`` under a ``DIRS`` root is imported during component backend setup rather than on first use.
That is what makes a ``@component.context`` visible from the first request, and it is the real cost of a shared UI kit, since each project pays the import of the whole kit at startup whether it renders one component or all of them.
Set ``LAZY_COMPONENT_MODULES`` to defer that bulk import to the first resolve of each component.

.. code-block:: python
   :caption: projects/admin/config/settings.py

   NEXT_FRAMEWORK = {
       "LAZY_COMPONENT_MODULES": True,
   }

A component whose template body lives in a module-level ``component`` string is still imported during discovery under the flag, because the scanner has to read that attribute.

Use the components
~~~~~~~~~~~~~~~~~~

.. code-block:: jinja
   :caption: any project template

   {% component "button" text="Save" variant="primary" %}

The framework resolves the component by name through the component visibility resolver.

Per project overrides
~~~~~~~~~~~~~~~~~~~~~

A project can override a shared component by placing a component with the same name in its own components root.

.. code-block:: text
   :caption: project override

   projects/admin/admin_app/pages/_components/button/component.djx

The project-local version wins because the visibility resolver scores the project's page-tree root and a global ``DIRS`` root equally, then breaks the tie in favour of the page-tree component.
A page-tree component shadows a same-name ``DIRS`` component at equal score, so the project's own copy overrides the shared one regardless of which root was registered first.

Static files
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

See also
--------

.. seealso::

   :doc:`/content/topics/multi-project` for the full multi-project layout and hot reload.
   :doc:`/content/topics/components` for the component lifecycle.
