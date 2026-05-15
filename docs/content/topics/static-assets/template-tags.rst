.. _topics-static-template-tags:

Static Template Tags
====================

The static pipeline exposes its output through two template tags.
``{% collect_styles %}`` emits every CSS asset collected so far.
``{% collect_scripts %}`` emits every JS and module asset collected so far.
This page covers the tag shapes, the placement rules, and the attributes that customise the emitted HTML.

.. contents::
   :local:
   :depth: 2

collect_styles
--------------

``{% collect_styles %}`` emits one ``<link rel="stylesheet">`` per CSS asset attached to the current request.

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

Place the tag inside ``<head>`` so the browser fetches stylesheets before rendering the body.

Block Form
~~~~~~~~~~

Use the block form to insert content before the collected output.

.. code-block:: jinja
   :caption: block form with vendor css

   {% #collect_styles %}
     <link rel="stylesheet" href="/static/vendor/reset.css">
   {% /collect_styles %}

The block content emits first, then the collected output follows.

Attributes
~~~~~~~~~~

Every keyword on the tag becomes an attribute on each emitted ``<link>`` element.

.. code-block:: jinja
   :caption: media targeted styles

   {% collect_styles media="screen" %}

Use this for media queries, integrity attributes, or any other valid HTML attribute.
The framework escapes the attribute value automatically.

collect_scripts
---------------

``{% collect_scripts %}`` emits one ``<script>`` per JS asset and one ``<script type="module">`` per module asset.

.. code-block:: jinja
   :caption: layout

   <!doctype html>
   <html>
     <body>
       {% block template %}{% endblock template %}
       {% collect_scripts %}
     </body>
   </html>

Place the tag at the end of ``<body>`` so the document body is parsed before scripts execute.

Block Form
~~~~~~~~~~

The block form lets you append content after the collected output.

.. code-block:: jinja
   :caption: block form with analytics

   {% #collect_scripts %}
     <script defer src="/static/analytics.js"></script>
   {% /collect_scripts %}

Attributes
~~~~~~~~~~

Keywords become attributes on each script element.

.. code-block:: jinja
   :caption: deferred scripts

   {% collect_scripts defer="defer" %}

Module Scripts
~~~~~~~~~~~~~~

Assets with kind ``module`` emit ``<script type="module">`` regardless of the attributes passed on the tag.
Override the rendering through a custom backend if you need a different format.

Placement Rules
---------------

Place each tag exactly once in the layout chain.
The framework injects the collected assets at every occurrence, which can lead to duplicate links if the tag appears in two layouts on the chain.

The recommended placement is on the outermost layout.

- ``{% collect_styles %}`` inside ``<head>``.
- ``{% collect_scripts %}`` at the bottom of ``<body>``.

Inner layouts can use the block form to add layout-local scripts or styles without competing with the main tag.

Inline Variants
---------------

The framework offers inline tags for one off scripts and styles that should live in the document but do not warrant a co-located file.

.. code-block:: jinja
   :caption: inline css

   {% #inline_style %}
     .note-list { padding: 0; }
   {% /inline_style %}

.. code-block:: jinja
   :caption: inline script

   {% #inline_script %}
     console.log("hello");
   {% /inline_script %}

Inline assets join the same collection slot.
They de-duplicate by content hash so the same inline snippet declared from two components emits exactly once.

Tag Loading
-----------

The framework loads the static template tags automatically through ``next.apps.templates.install``.
Templates therefore do not need to call ``{% load next_static %}``.

This also applies to ``{% form %}`` and ``{% component %}``.
See :doc:`/content/ref/template-tags` for the complete list.

Common Patterns
---------------

Critical CSS at the Top
~~~~~~~~~~~~~~~~~~~~~~~

Use the block form of ``{% #collect_styles %}`` to inline a small critical stylesheet before the collected link tags.

Module Preloads
~~~~~~~~~~~~~~~

Combine module assets with ``<link rel="modulepreload">`` to start the import graph early.
Add the preload through the block form and let ``{% collect_scripts %}`` emit the actual modules.

Per Section Tag Placement
~~~~~~~~~~~~~~~~~~~~~~~~~

Move ``{% collect_scripts %}`` into an inner layout when the section ships heavy scripts.
The root layout still ships its own scripts through its own tag if you keep two layouts each with one tag.

See Also
--------

.. seealso::

   :doc:`co-located-files` for what becomes an asset.
   :doc:`deduplication` for how duplicates are avoided.
   :doc:`backends` for swapping the rendered output.
   :doc:`/content/ref/template-tags` for the full tag catalog.
