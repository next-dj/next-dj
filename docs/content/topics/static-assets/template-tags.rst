.. _topics-static-template-tags:

Static Template Tags
====================

The static pipeline exposes four template tags.
``{% collect_styles %}`` and ``{% collect_scripts %}`` mark placeholder slots in the layout.
``{% use_style %}`` and ``{% use_script %}`` register external or inline assets from a template.

.. contents::
   :local:
   :depth: 2

collect_styles
--------------

``{% collect_styles %}`` marks the slot where the static manager injects every collected CSS link tag.

.. code-block:: jinja
   :caption: layout

   <!doctype html>
   <html>
     <head>
       <title>{{ site_name }}</title>
       {% collect_styles %}
     </head>
     <body>
       {% block template %}{% endblock template %}
     </body>
   </html>

The tag takes no arguments.
It emits a placeholder token at parse time.
After the page renders, the static manager replaces the token with the rendered link tags for every asset in the ``styles`` slot.

Place the tag inside ``<head>`` so the browser fetches stylesheets before rendering the body.

collect_scripts
---------------

``{% collect_scripts %}`` marks the slot for collected JS and module tags.

.. code-block:: jinja
   :caption: layout

   <body>
     {% block template %}{% endblock template %}
     {% collect_scripts %}
   </body>

The tag takes no arguments.
Assets of kind ``js`` and kind ``module`` both land in the ``scripts`` slot.
Place the tag at the end of ``<body>`` so the document body is parsed before scripts execute.

use_style
---------

``{% use_style %}`` registers an external CSS URL on the active collector.

.. code-block:: jinja
   :caption: external stylesheet

   {% use_style "https://cdn.example.com/reset.css" %}

The asset is prepended to the collector so shared dependencies load before co-located styles.
The CSS cascade therefore flows from generic dependencies to page specific styling.

use_script
----------

``{% use_script %}`` registers an external JS URL on the active collector.

.. code-block:: jinja
   :caption: external script

   {% use_script "https://cdn.example.com/vendor.js" %}

The asset is prepended to the collector the same way as ``use_style``.

Inline Blocks
-------------

``{% use_style %}`` and ``{% use_script %}`` also have a block form for inline content.
Prepend a hash sign to open the block and pair it with the matching close tag.

.. code-block:: jinja
   :caption: inline css

   {% #use_style %}
     .note-list { padding: 0; }
   {% /use_style %}

.. code-block:: jinja
   :caption: inline script

   {% #use_script %}
     console.log("hello");
   {% /use_script %}

The block body is rendered with the current template context, so inline blocks can interpolate page variables.
Blank only blocks are dropped.
The collector deduplicates inline entries by the rendered body, so two identical blocks collapse to one.

Placement Rules
---------------

Place ``{% collect_styles %}`` and ``{% collect_scripts %}`` exactly once each in the layout chain.
The recommended placement is the outermost layout.

- ``{% collect_styles %}`` inside ``<head>``.
- ``{% collect_scripts %}`` at the bottom of ``<body>``.

Customising the Tag Output
--------------------------

The ``collect`` tags do not accept HTML attributes.
The rendered ``<link>``, ``<script>``, and ``<script type="module">`` markup comes from the active backend.
Customise it through the backend ``OPTIONS`` keys ``css_tag``, ``js_tag``, and ``module_tag``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {
                   "css_tag": '<link rel="stylesheet" href="{url}" media="screen">',
                   "js_tag": '<script src="{url}" defer></script>',
               },
           }
       ]
   }

The format string must contain the ``{url}`` placeholder.
See :doc:`backends` for the full backend surface.

Tag Loading
-----------

The framework loads the static template tags as Django builtins through ``next.apps.templates``.
Templates do not need a ``{% load %}`` statement.
The same applies to ``{% form %}`` and ``{% component %}``.

Common Patterns
---------------

Vendor CSS Before Component Styles
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``{% use_style %}`` for a vendor stylesheet.
The prepend behaviour guarantees the vendor file loads before any co-located ``component.css``.

Critical Inline CSS
~~~~~~~~~~~~~~~~~~~

Use the inline block form of ``{% #use_style %}`` for a small critical stylesheet that should ship in the document.

Per Page Script
~~~~~~~~~~~~~~~

Use the inline block form of ``{% #use_script %}`` for a one off script that interpolates page context.

See Also
--------

.. seealso::

   :doc:`co-located-files` for what becomes an asset.
   :doc:`deduplication` for how duplicates are avoided.
   :doc:`backends` for the rendered tag output.
   :doc:`/content/ref/template-tags` for the full tag catalog.
