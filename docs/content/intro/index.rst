.. _intro:

Getting started
===============

This section gets you from zero to a running next.dj project.
Read the overview to learn the mental model, map your Django vocabulary onto it, then install the package.
The six tutorial parts build a small Notes application from there.

.. rubric:: Read first

:doc:`overview`
   The mental model behind next.dj.
   Read it once to understand pages, layouts, components, and actions.

:doc:`from-django`
   A mapping table from the Django concepts you already know to their next.dj counterparts.

:doc:`install`
   Install the package, register it in Django, and serve a single page.

.. rubric:: Build the Notes app

:doc:`tutorial01`
   Create the Notes application, model the data, and serve the index page.

:doc:`tutorial02`
   Wrap pages in a layout and share data through context.

:doc:`tutorial03`
   Extract a component and ship co-located CSS and JS.

:doc:`tutorial04`
   Render forms and dispatch actions to create, edit, and delete notes.

:doc:`tutorial05`
   Test pages end to end and use the development server.

:doc:`tutorial06`
   Make the index live with zones and partial rendering, with a no-JavaScript fallback.

:doc:`limitations`
   The deliberate boundaries of the framework, from the synchronous pipeline to the single partial backend.

:doc:`whatsnext`
   Where to go for deeper topics, recipes, and reference material.

.. toctree::
   :hidden:
   :maxdepth: 1

   overview
   from-django
   install
   tutorial01
   tutorial02
   tutorial03
   tutorial04
   tutorial05
   tutorial06
   limitations
   whatsnext
