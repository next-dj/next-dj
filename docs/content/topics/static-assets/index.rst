.. _topics-static-assets:

Static Assets
=============

The static pipeline discovers co-located CSS, JS, and module files, deduplicates them across requests, and injects them into HTML at the location of ``{% collect_styles %}`` and ``{% collect_scripts %}`` template tags.
The pipeline is fully pluggable through asset kinds, custom stems, and backends.

.. rubric:: Concepts

:doc:`overview`
   The mental model behind the pipeline.

:doc:`co-located-files`
   How asset files are paired with pages and components.

:doc:`template-tags`
   Template tags that emit the collected output.

.. rubric:: Mechanics

:doc:`deduplication`
   How the framework avoids emitting the same asset twice.

:doc:`asset-kinds`
   Built-in kinds and how to register new ones.

:doc:`custom-stems`
   Recognise additional filenames as component assets.

.. rubric:: Browser side

:doc:`js-context`
   Exposing context to the browser through the ``Next`` object.

.. rubric:: Extending

:doc:`backends`
   Customizing the collector and the injection format through backends.

:doc:`signals`
   Every signal the static subsystem emits.

.. toctree::
   :hidden:
   :maxdepth: 1

   overview
   co-located-files
   template-tags
   deduplication
   asset-kinds
   custom-stems
   js-context
   backends
   signals
