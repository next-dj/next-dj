.. _topics-static-assets:

Static assets
=============

The static pipeline discovers co-located CSS, JS, and module files, deduplicates them within a request, and injects them into HTML.

.. rubric:: Concepts

:doc:`overview`
   The mental model behind the pipeline.

:doc:`co-located-files`
   How asset files are paired with pages and components.

.. rubric:: Authoring

:doc:`template-tags`
   Template tags that emit the collected output.

:doc:`name-resolution`
   When an asset reference is a staticfiles name and when it is a finished URL.

:ref:`Named assets via module lists <topics-static-module-lists>`
   Declaring further assets from ``page.py`` and ``component.py``.

:doc:`js-context`
   Exposing context to the browser through the ``Next`` object.

.. rubric:: Mechanics

:doc:`deduplication`
   How the framework avoids emitting the same asset twice.

:doc:`asset-kinds`
   Built-in kinds and how to register new ones.

:doc:`custom-stems`
   Recognise additional filenames as page, layout, or component assets.

.. rubric:: Extending

:doc:`backends`
   Resolving asset URLs and customising the rendered tags through a static backend.

:doc:`signals`
   Every signal the static subsystem emits.

.. toctree::
   :hidden:
   :maxdepth: 1

   overview
   co-located-files
   template-tags
   name-resolution
   js-context
   deduplication
   asset-kinds
   custom-stems
   backends
   signals
