.. _topics-static-assets:

Static Assets
=============

The static pipeline discovers co-located CSS, JS, and module files, deduplicates them across requests, and injects them into HTML.
The default injection points are the ``collect_*`` template tags, which mark placeholder slots in the layout.
Extra slots register through ``default_placeholders.register``, so projects can add their own injection points beyond ``styles`` and ``scripts``.
The pipeline is fully pluggable through asset kinds, custom stems, and backends.

.. rubric:: Concepts

:doc:`overview`
   The mental model behind the pipeline.

:doc:`co-located-files`
   How asset files are paired with pages and components.

.. rubric:: Authoring

:doc:`template-tags`
   Template tags that emit the collected output.

:doc:`js-context`
   Exposing context to the browser through the ``Next`` object.

.. rubric:: Mechanics

:doc:`deduplication`
   How the framework avoids emitting the same asset twice.

:doc:`asset-kinds`
   Built-in kinds and how to register new ones.

:doc:`custom-stems`
   Recognise additional filenames as component assets.

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
