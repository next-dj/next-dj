.. _topics-static-template-tags:

Static template tags
====================

The static pipeline registers six Django template tags plus two inline block forms.
``{% collect_styles %}`` and ``{% collect_scripts %}`` mark placeholder slots in the layout.
``{% use_style %}``, ``{% use_script %}``, and ``{% use_module %}`` register an asset on the active collector.
``{% asset %}`` returns one URL for a raw attribute and registers nothing.
``{% #use_style %}`` and ``{% #use_script %}`` are block forms that capture an inline body.

.. contents::
   :local:
   :depth: 2

Names and URLs
--------------

Every tag that takes an asset reference reads it the same way.
A reference carrying no scheme, no host, no query, and no fragment, whose path is non-empty and does not start with a slash, is a Django staticfiles name and is resolved through the configured storage.
Any other reference reaches the document as written, so a CDN URL, a root-relative path, and a data URL all pass through untouched.

.. code-block:: jinja
   :caption: notes/pages/template.djx

   {% use_style "site/tokens.css" %}
   {% use_style "https://cdn.example.com/reset.css" %}

The first tag emits ``/static/site/tokens.css`` under a plain ``STATIC_URL`` and the hashed filename under a manifest storage.
The second emits the URL it was given.
:doc:`name-resolution` states the rule in full, lists every shape that passes through, and covers what happens when a name resolves to nothing.

collect_styles
--------------

``{% collect_styles %}`` marks the slot where the static manager injects every collected CSS link tag.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   <!doctype html>
   <html>
     <head>
       <title>{{ site_name }}</title>
       {% collect_styles %}
     </head>
     <body>
       {% template %}
     </body>
   </html>

The tag takes no arguments.
It emits the ``<!-- next:styles -->`` placeholder token when the template renders.
After the page renders, the static manager replaces the token with the rendered link tags for every asset in the ``styles`` slot.

Place the tag inside ``<head>`` so the browser fetches stylesheets before rendering the body.

collect_scripts
---------------

``{% collect_scripts %}`` marks the slot for collected JS and module tags.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   <body>
     {% template %}
     {% collect_scripts %}
   </body>

The tag takes no arguments.
Assets of kind ``js`` and kind ``module`` both land in the ``scripts`` slot.
Place the tag at the end of ``<body>`` so the document body is parsed before scripts execute.

Under the default ``AUTO`` script injection policy this tag is also where the ``next.min.js`` runtime and the ``Next._init`` call land, so a layout chain without it leaves ``window.Next`` undefined.
See :ref:`Runtime script options <topics-static-js-runtime-script-options>` for the policy that controls the injection.

use_style
---------

``{% use_style %}`` registers a CSS asset on the active collector.

.. code-block:: jinja
   :caption: notes/pages/template.djx

   {% use_style "site/tokens.css" %}
   {% use_style "https://cdn.example.com/reset.css" %}

The argument is a staticfiles name or a finished URL, see `Names and URLs`_.
The asset is prepended to the collector so shared dependencies load before co-located styles.
The CSS cascade therefore flows from generic dependencies to page specific styling.
The tag takes no ``kind`` argument and always registers a ``css`` asset, so an asset of another kind goes through ``{% use_script %}`` with an explicit ``kind``.

use_script
----------

``{% use_script %}`` registers an asset on the active collector under the given kind.

.. code-block:: jinja
   :caption: notes/pages/template.djx

   {% use_script "site/vendor.js" %}
   {% use_script "https://cdn.example.com/vendor.js" %}

The first argument is a staticfiles name or a finished URL, read the same way as in ``use_style``.
The asset is prepended to the collector the same way as ``use_style``.

The optional ``kind`` argument defaults to ``js``, which renders a classic ``<script>`` tag.
Any other registered kind works too, and the registry decides both the slot the asset lands in and the backend renderer that builds its tag.

.. code-block:: jinja
   :caption: notes/pages/template.djx

   {% use_script "site/vendor.mjs" kind="module" %}
   {% use_script "https://cdn.example.com/inter.woff2" kind="font" %}

A custom kind registered through ``KindRegistry.register`` therefore needs no template tag of its own, see :doc:`asset-kinds`.
A kind that was never registered raises ``KeyError`` out of the render, so a typo surfaces on the first request instead of dropping the asset.

use_module
----------

``{% use_module %}`` is the shorthand for ``{% use_script %}`` with ``kind="module"``.

.. code-block:: jinja
   :caption: notes/pages/template.djx

   {% use_module "site/vendor.mjs" %}

The asset registers under kind ``module`` and renders as a ``<script type="module">`` tag through the backend ``render_module_tag`` hook.
The browser defers a module script, so the prepend controls markup order, not execution order relative to classic scripts.
The tag has no ``#use_module`` block form.
The ``module`` kind names no inline wrapper element, so a block body would land in the slot verbatim instead of as an inline ES module, see :doc:`asset-kinds`.

The same asset registered under two kinds is two assets, so ``{% use_script %}`` and ``{% use_module %}`` on one reference emit both a classic and a module tag, see :doc:`deduplication`.

.. note::

   The ``use_*`` registration tags and their block forms need the request-scoped collector that the page pipeline puts in the template context.
   A template rendered outside that pipeline, for example through ``render_to_string`` in a plain view, silently registers nothing and emits nothing.
   ``{% asset %}`` is the exception, because it returns a value instead of registering one.

Inline blocks
-------------

``{% use_style %}`` and ``{% use_script %}`` also have a block form for inline content.
Prepend a hash sign to open the block and pair it with the matching close tag.

.. code-block:: jinja
   :caption: notes/pages/template.djx

   {% #use_style %}
     .note-list { padding: 0; }
   {% /use_style %}

.. code-block:: jinja
   :caption: notes/pages/template.djx

   {% #use_script %}
     console.log("hello");
   {% /use_script %}

The framework wraps a ``{% #use_style %}`` body in a ``<style>`` element and a ``{% #use_script %}`` body in a ``<script>`` element on injection.
The author writes only the inner CSS or JS, not the surrounding tag.
Neither block form takes a ``kind`` argument, and the two are fixed to ``css`` and ``js``.

The block body is rendered with the current template context, so inline blocks can interpolate page variables.
Blank only blocks are dropped.
The collector deduplicates inline entries by the rendered body, so two identical blocks collapse to one.

.. note::

   A block form needs the same request-scoped collector as the void form.
   A template rendered outside the page pipeline registers nothing and emits nothing, and the body never reaches the document.

asset
-----

``{% asset %}`` returns the public URL of one reference as a string, for a raw ``href``, ``src``, or ``content`` attribute.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   <link rel="icon" href="{% asset "site/favicon.svg" %}">

The first argument is a staticfiles name, a finished URL, or a context variable holding either.
An unset or empty variable renders nothing, so a missing value never becomes a link back to the current page.
The tag runs the same resolution the registration tags run and then the per-request URL hook of the active backend, so a named file carries the manifest hash and a per-tenant prefix exactly as a collected asset does.
It registers nothing on the collector, so it also works in a render that has none, such as a template rendered through ``render_to_string`` in a plain view.

``{% asset %}`` is the recommended spelling for an asset URL in a next.dj template.
A paired benchmark on one machine measures it at roughly 1.9 times cheaper than Django's ``{% static %}`` for the same name, because the backend answers a repeat from its memo while the Django tag asks storage on every render.
Resolving a reference that is already a URL, the shape a template written today holds, costs about 125 nanoseconds per tag on the same run.
Both numbers are indicative rather than contractual, and the pair they come from is ``tests/benchmarks/static/test_bench_resolve.py``.

Django's ``{% static %}`` keeps working and is the better choice where the value must be identical for every request.
``{% asset %}`` output can vary per request, because ``asset_url`` receives the request, and a value that varies must not be baked into a ``{% cache %}`` fragment keyed on something else.

The ``as`` form binds the URL to a template variable.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   {% asset "site/app.css" as app_css %}
   <link rel="preload" as="style" href="{{ app_css }}">
   <link rel="stylesheet" href="{{ app_css }}">

.. note::

   The returned URL is HTML escaped like any other template output, so an ampersand between two query parameters renders as ``&amp;``.
   That is the correct spelling inside an attribute, and the browser reads it as a single ampersand.

Versioning a URL
~~~~~~~~~~~~~~~~

The optional ``version`` argument, a literal or a context value, appends a ``v`` query parameter to the URL.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   {% asset "site/app.css" version=build_id %}

The value is coerced to a string and percent encoded, and a URL that already carries a query gains one more pair rather than a second ``?``.
The version is appended after the backend hook, so a rewritten URL keeps it.

``STATIC_VERSION`` in ``NEXT_FRAMEWORK`` sets the same parameter for every URL the pipeline renders, co-located files, module lists, tag assets, and the ``next.min.js`` runtime included.
A ``version`` on a single ``{% asset %}`` call wins over the project value for that one URL, and ``version=""`` is the way to spell "no version here" while the project value is set.

.. warning::

   A backend that returns a signed URL does not combine with ``version`` or ``STATIC_VERSION``.
   A signature covers the query string, so an appended parameter invalidates it, and such a backend carries its own version instead.

:doc:`/content/deployment/static-files` covers when a project wants a global version and why a content hash is the better answer.

Placement rules
---------------

Place each ``{% collect_styles %}`` and ``{% collect_scripts %}`` tag exactly once in the layout chain.
The manager replaces every occurrence of the slot token with the same rendered output, so a tag that appears twice emits every collected asset twice in the final HTML.
The recommended placement is the outermost layout.

- ``{% collect_styles %}`` inside ``<head>``.
- ``{% collect_scripts %}`` at the bottom of ``<body>``.

Customising the tag output
--------------------------

The ``collect`` tags accept no HTML attributes, and the rendered ``<link>``, ``<script>``, and ``<script type="module">`` markup comes from the active backend.
See :doc:`backends` for the ``css_tag``, ``js_tag``, and ``module_tag`` ``OPTIONS`` keys.

Tag loading
-----------

The framework loads the static template tags as Django builtins through ``next.apps.templates.install``.
Templates do not need a ``{% load %}`` statement.
The same applies to ``{% form %}`` and ``{% component %}``.

Common patterns
---------------

Vendor CSS before component styles
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``{% use_style %}`` for a vendor stylesheet, a name for a file the project ships and a URL for one a third party hosts.
The prepend behaviour guarantees the vendor file loads before any co-located ``component.css``.

Site-wide stylesheet
~~~~~~~~~~~~~~~~~~~~

Write ``{% use_style %}`` once in the root ``layout.djx``, which every page below it inherits, see :doc:`/content/howto/ship-a-site-wide-stylesheet`.

Critical inline CSS
~~~~~~~~~~~~~~~~~~~

Use the inline block form of ``{% #use_style %}`` for a small critical stylesheet that should ship in the document.

Per-page script
~~~~~~~~~~~~~~~

Use the inline block form of ``{% #use_script %}`` for a one off script that interpolates page context.

See also
--------

.. seealso::

   :doc:`co-located-files` for what becomes an asset.
   :doc:`name-resolution` for the rule that decides a name from a URL.
   :doc:`deduplication` for how duplicates are avoided.
   :doc:`backends` for the rendered tag output.
   :doc:`/content/ref/template-tags` for the full tag catalog.
