Static assets
=============

next.dj automatically discovers CSS and JavaScript files that live next to your
pages, layouts, and components, plus any extra URLs you declare from Python or
templates. The collected assets are deduplicated, ordered, and injected into
two slots on the rendered HTML. You stop hand-wiring ``<link>`` and
``<script>`` tags into every layout.

Overview
--------

The static subsystem covers three complementary use cases:

- **Co-located files.** Drop ``layout.css``/``layout.js`` next to a
  ``layout.djx``, ``template.css``/``template.js`` next to a ``template.djx``,
  and ``component.css``/``component.js`` next to a ``component.djx``. They are
  picked up automatically and served by the framework.
- **Module-level URLs.** Declare ``styles`` and ``scripts`` lists in any
  ``page.py`` or ``component.py`` to add external assets (CDN, custom hosting)
  for that page or component.
- **Template tags.** Use ``{% use_style %}`` and ``{% use_script %}`` to
  register external assets directly from a layout or template. These are
  perfect for shared third-party libraries like Bootstrap or Chart.js. Their
  block forms ``{% #use_style %}`` … ``{% /use_style %}`` and
  ``{% #use_script %}`` … ``{% /use_script %}`` capture an inline ``<style>``
  or ``<script>`` body and hoist it straight into the corresponding slot.

Two slots in your HTML drive the injection: ``{% collect_styles %}`` (in
``<head>``) and ``{% collect_scripts %}`` (just before ``</body>``). Everything
referenced during rendering, directly or through nested components, flows
into those slots in deterministic order, with duplicates removed.

How it works
------------

Rendering happens in two phases:

1. **Render phase.** While ``Page.render`` walks layouts → template → nested
   components, a per-request :class:`~next.static.StaticCollector` accumulates
   asset references. Each ``{% component %}`` tag forwards its component to
   :meth:`~next.static.StaticManager.discover_component_assets` so co-located
   files and module-level lists are picked up. The two ``collect_*`` template
   tags emit lightweight HTML comment placeholders (``<!-- next:styles -->``
   and ``<!-- next:scripts -->``).
2. **Inject phase.** After rendering finishes, the manager rewrites both
   placeholders with concatenated ``<link>`` and ``<script>`` tags built by
   the active backend. In the default configuration, co-located files resolve
   through Django ``staticfiles_storage`` under the ``next/`` namespace, so
   ``collectstatic`` + Manifest + S3/CDN settings apply automatically.

Ordering is **cascade-friendly**. Generic dependencies come first so
page- and component-specific rules can override them:

1. ``{% use_style %}`` / ``{% use_script %}`` declarations (layout-level
   shared dependencies like Bootstrap), in the order they were registered.
2. Layout co-located files (``layout.css`` / ``layout.js``), outermost layout
   first.
3. Template file of the page itself (``template.css`` / ``template.js``).
4. Module-level ``styles`` / ``scripts`` declared in ``page.py``.
5. Component co-located files (``component.css`` / ``component.js``) in
   depth-first render order.
6. Module-level ``styles`` / ``scripts`` declared in ``component.py`` (right
   after that component's files).
7. Inline blocks from ``{% #use_style %}`` / ``{% #use_script %}`` in
   registration order, appended last so that inline boot code runs after
   every dependency has loaded.

The ordering mirrors normal CSS cascade: shared libraries flow in at the
front, layout-wide styling sits above page-level styling, and component-level
styling can tweak everything above it. URLs already seen on the collector are
silently skipped, so adding the same Bootstrap CSS in three components only
emits one ``<link>``.

Architecture
~~~~~~~~~~~~

The pipeline below shows the modules of :mod:`next.static` and how a single
request flows through them:

.. code-block:: text

   ┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
   │  Template    │──▶│ next_static tags │──▶│ StaticCollector  │
   │  (.djx)      │     │ collect/use/#use │     │ (dedup + order)  │
   └──────────────┘     └──────────────────┘     └────────┬─────────┘
                                                          │
          ┌───────────────────────────────────────────────┤
          │                                               ▼
   ┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
   │ AssetDiscov. │──▶│ StaticBackend    │──▶│ StaticManager    │
   │ pages/comps  │     │ register_file    │     │ .inject(html)    │
   │ module lists │     │ render_*_tag     │     │ placeholders →   │
   └──────────────┘     └──────────────────┘     │ <link>/<script>  │
                                                 └──────────────────┘

Signals emitted along the way:

- :data:`~next.static.signals.asset_registered` fires inside
  :class:`~next.static.AssetDiscovery` after a co-located file is
  successfully registered with the backend.
- :data:`~next.static.signals.collector_finalized` fires inside
  :meth:`~next.static.StaticManager.inject` just before the HTML is
  rewritten.
- :data:`~next.static.signals.html_injected` fires right after the placeholders
  have been replaced with real tags.
- :data:`~next.static.signals.backend_loaded` fires every time
  :class:`~next.static.StaticsFactory` instantiates a backend from config.

See :ref:`static-signals` for the full signatures and usage recipes.

Quick start
-----------

A minimal example with one layout, one page, one component:

**1. Configure ``DEFAULT_STATIC_BACKENDS``** (optional, the default backend is
used automatically when the key is missing).

.. code-block:: python

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {},
           },
       ],
   }

**2. Add the two slots to your root layout.**

.. code-block:: django

   <!DOCTYPE html>
   <html>
   <head>
       <meta charset="utf-8">
       <title>My site</title>
       {% use_style "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" %}
       {% collect_styles %}
   </head>
   <body>
       {% block template %}{% endblock template %}
       {% use_script "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js" %}
       {% collect_scripts %}
   </body>
   </html>

**3. Drop co-located files anywhere in the page tree.** For
``myapp/pages/dashboard/template.djx`` you may add ``template.css`` and
``template.js`` next to it. For ``_components/widget/component.djx`` you may
add ``component.css`` and ``component.js`` next to it.

**4. Add module-level lists for page-specific external assets.**

.. code-block:: python

   styles = [
       "https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap",
   ]
   scripts: list[str] = []

That is the entire setup. Reload the page and the rendered HTML will contain
deduplicated ``<link>`` tags inside ``<head>`` and ``<script>`` tags before
``</body>`` in the order described above.

Co-located files
----------------

The framework looks for **fixed file names** in three locations.

.. list-table::
   :header-rows: 1
   :widths: 24 22 54

   * - Where
     - Files
     - Logical URL
   * - Next to ``layout.djx``
     - ``layout.css``, ``layout.js``
     - ``next/<route>/layout.{css,js}`` (or ``next/layout.{css,js}`` at the page root)
   * - Next to ``template.djx``
     - ``template.css``, ``template.js``
     - ``next/<route>.{css,js}`` (or ``next/index.{css,js}`` at the page root)
   * - Next to ``component.djx`` / ``component.py``
     - ``component.css``, ``component.js``
     - ``next/components/<name>.{css,js}``

The naming is intentional: assets follow the **route** of the page they belong
to (and the component **name** for components), not a hashed filesystem path.
This keeps URLs predictable and easy to debug.

Examples:

- ``myapp/pages/about/template.css`` →
  ``next/about.css`` (then hashed by manifest if enabled)
- ``myapp/pages/blog/layout.djx`` + ``layout.js`` →
  ``next/blog/layout.js`` (then hashed by manifest if enabled)
- ``myapp/pages/_components/card/component.css`` →
  ``next/components/card.css`` (then hashed by manifest if enabled)
- ``myapp/pages/dashboard/_components/chart/component.js`` →
  ``next/components/chart.js`` (then hashed by manifest if enabled)

.. tip::

   Co-located files mean **what is rendered together lives together**. Move
   the page and its ``template.css`` follows automatically. No central
   registry to update.

Module-level ``styles`` and ``scripts``
----------------------------------------

Inside any ``page.py`` or ``component.py`` you can declare two module-level
attributes:

.. code-block:: python

   styles = [
       "https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap",
       "https://cdn.example.com/themes/dark.css",
   ]
   scripts = [
       "https://cdn.example.com/analytics.min.js",
   ]

Both are plain Python lists of URLs (relative or absolute). Module-level lists
are added **after** the co-located files for the same scope. The file is the
page's own asset, the list is its dependencies. Non-string and empty entries
are filtered out.

.. tip::

   Use module lists for assets that are conceptually owned by **this** page or
   component (e.g. a page-specific font or a component-specific charting
   library). Put **shared** site-wide dependencies in the layout via
   ``{% use_style %}`` / ``{% use_script %}`` instead. Those always land at
   the top of the collected list so page-level styling can override them.

Template tags
-------------

The static template tags ship with the ``next_static`` library and are
registered as builtins, so no ``{% load %}`` is required.

``{% collect_styles %}``
~~~~~~~~~~~~~~~~~~~~~~~~

Marks the slot where collected ``<link rel="stylesheet">`` tags will be
inserted. Place it inside ``<head>`` of your root layout.

.. code-block:: django

   <head>
       <title>My site</title>
       {% collect_styles %}
   </head>

``{% collect_scripts %}``
~~~~~~~~~~~~~~~~~~~~~~~~~

Marks the slot for collected ``<script>`` tags. Place it just before
``</body>`` so scripts do not block first paint.

.. code-block:: django

   <body>
       {% block template %}{% endblock template %}
       {% collect_scripts %}
   </body>

``{% use_style "URL" %}``
~~~~~~~~~~~~~~~~~~~~~~~~~

Registers an external CSS URL on the active collector. Useful for assets
shared across every page, such as Bootstrap or a global font. Emits **no**
markup at the call site. The URL is rendered inside the
``{% collect_styles %}`` slot together with all other styles.

.. code-block:: django

   {% use_style "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" %}

``{% use_script "URL" %}``
~~~~~~~~~~~~~~~~~~~~~~~~~~

Same as ``{% use_style %}`` but for JS. Combine the two in your layout to
declare site-wide third-party libraries in one place.

.. code-block:: django

   {% use_script "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js" %}

.. tip::

   ``{% use_style %}`` and ``{% use_script %}`` are silent no-ops when there
   is no active collector (for example when rendering a template manually
   without going through ``Page.render``). This makes them safe to drop into
   any reusable template.

``{% #use_style %}`` / ``{% #use_script %}`` (block form)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The block form mirrors ``{% #component %}``: ``{% #use_script %}`` opens,
``{% /use_script %}`` closes, and everything inside is **hoisted** into the
``{% collect_scripts %}`` slot. The body is rendered with the current
template context, so you can still interpolate ``{{ variable }}`` values
from the page. The block emits **nothing** at the call site. Only the
final scripts slot receives the captured HTML.

.. code-block:: django

   <div id="widget-{{ id }}"></div>

   {% #use_script %}
   <script type="module">
       const el = document.getElementById("widget-{{ id }}");
       el.dataset.boot = "ready";
   </script>
   {% /use_script %}

After rendering, the ``<script type="module">`` block lives inside
``{% collect_scripts %}`` together with every other JS asset, while the
``<div>`` stays where the component drew it. The same pattern works for
inline ``<style>`` via ``{% #use_style %}`` … ``{% /use_style %}``.

Order and deduplication for inline blocks:

- **Append, not prepend.** Unlike the URL form, block bodies are treated as
  consumers of the earlier dependencies and always land *after* URL-form
  ``use_script`` / ``use_style`` entries and after co-located ``*.js`` /
  ``*.css`` files, in registration order.
- **Content-based deduplication.** Each block's dedup key is its rendered
  body. Two blocks whose templates produce byte-identical HTML collapse to
  a single entry in the slot, even if they were written in different
  components or rendered via different ``{% component %}`` invocations. If
  a block interpolates variable context (``{{ id }}``, ``{{ label }}``),
  the resulting HTML differs per render and both copies land in the slot.
  Whitespace-only bodies are skipped.
- **Verbatim emission.** The captured HTML is written into the slot as-is,
  so you can include custom ``<script>`` attributes (``type="module"``,
  ``type="text/babel"``, ``nonce="…"``, ``defer``) or whole ``<style>``
  blocks without any wrapping done by the framework.

.. tip::

   Because inline blocks append, the natural layering is: layout ``use_*``
   shared deps → component-level ``use_*`` deps → co-located ``layout.js``
   / ``template.js`` / ``component.js`` → inline ``{% #use_script %}``
   bodies. That matches the typical "load libraries, load code, then boot"
   runtime order.

Backends and settings
---------------------

Backends are configured under the top-level ``NEXT_FRAMEWORK`` dict in Django
settings, with the key ``DEFAULT_STATIC_BACKENDS``: a list of backend configs
in the same shape as the page and component backends.

.. code-block:: python

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {},
           },
       ],
   }

Each entry is a dict that is passed unchanged into the backend constructor:

- ``BACKEND`` (str). Dotted import path of the backend class. Defaults to
  ``"next.static.StaticFilesBackend"``.
- ``OPTIONS`` (dict). Backend-specific options. ``StaticFilesBackend`` reads:

  - ``css_tag`` (str). Format string for CSS link tags. ``{url}`` is
    substituted. Default: ``'<link rel="stylesheet" href="{url}">'``.
  - ``js_tag`` (str). Format string for JS script tags. ``{url}`` is
    substituted. Default: ``'<script src="{url}"></script>'``.

If ``DEFAULT_STATIC_BACKENDS`` is missing, empty, or contains no usable
entries, the framework falls back to ``StaticFilesBackend()`` so static
URLs still flow through Django staticfiles.

Customizing tag templates
~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``OPTIONS`` to add attributes (``defer``, ``crossorigin``, ``data-*``)
without writing a custom backend:

.. code-block:: python

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {
                   "css_tag": '<link rel="stylesheet" href="{url}" crossorigin>',
                   "js_tag": '<script src="{url}" defer></script>',
               },
           },
       ],
   }

Staticfiles and collectstatic
-----------------------------

Co-located files are mapped into the ``next/`` namespace and resolved via
``django.contrib.staticfiles.storage.staticfiles_storage.url(...)``.
This means:

- ``collectstatic`` sees the files through ``next.static.NextStaticFilesFinder``,
- Manifest storages rewrite URLs to hashed file names,
- S3/CDN backends return public bucket/CDN URLs in final HTML automatically.

If a file is missing from the manifest, ``StaticFilesBackend`` raises a
clear runtime error pointing to ``collectstatic``/finder setup.

Practical walkthroughs
----------------------

Site-wide assets in the layout
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Declare shared third-party libraries once at the top of the layout:

.. code-block:: django

   <!DOCTYPE html>
   <html>
   <head>
       <meta charset="utf-8">
       <title>My app</title>
       {% use_style "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" %}
       {% collect_styles %}
   </head>
   <body>
       {% block template %}{% endblock template %}
       {% use_script "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js" %}
       {% collect_scripts %}
   </body>
   </html>

Every page in the tree inherits Bootstrap automatically. No duplicate ``<link>``
tags will appear even if a child page or component requests the same URL.

Page-specific styles and scripts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Co-locate ``template.css`` next to ``page.py`` for the page's own styling, and
declare external assets in the module:

.. code-block:: python

   styles = [
       "https://fonts.googleapis.com/css2?family=JetBrains+Mono&display=swap",
   ]
   scripts: list[str] = []

   from next.pages import context

   @context("page_title")
   def get_page_title() -> str:
       return "Dashboard"

Output (excerpt):

.. code-block:: html

   <head>
       <link rel="stylesheet" href="https://cdn.jsdelivr.net/.../bootstrap.min.css">
       <link rel="stylesheet" href="/static/next/layout.abcd1234.css">
       <link rel="stylesheet" href="/static/next/dashboard.efgh5678.css">
       <link rel="stylesheet" href="https://fonts.googleapis.com/...">
   </head>

The layout's shared ``use_style`` dependency comes first, followed by the
layout file (outermost), the page's template file, and finally the page's
module list. Each level can override the styling above it.

Component with co-located CSS / JS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Composite component ``_components/chart/`` with all four files:

.. code-block:: text

   _components/chart/
       component.djx
       component.css
       component.js
       component.py

In ``component.py`` add a CDN dependency:

.. code-block:: python

   from next.components import context

   scripts = [
       "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js",
   ]

   @context("chart_labels")
   def chart_labels():
       return ["Jan", "Feb", "Mar"]

Whenever a page renders ``{% component "chart" %}``, the framework registers
``component.css``, ``component.js``, and the Chart.js CDN URL in render order.
Render the same component three times on a page and each URL still appears
exactly once.

.. tip::

   In the final HTML, layout-level ``use_script`` dependencies come first,
   followed by layout / template / component files in render order, with each
   scope's module-level list immediately after its own file. That means a
   component's own ``component.js`` runs **before** the ``scripts`` it
   declares in ``component.py``. Wrap any initialization that needs the CDN
   dependency in ``DOMContentLoaded`` (or a late-running event) so it executes
   after every ``<script>`` has finished loading.

Complex integrations. React + Babel counter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Nothing in the pipeline is tied to Bootstrap. Co-located files, module
lists and ``{% use_script %}`` declarations compose cleanly with any
third-party stack. The following composite component mounts a click counter
written in JSX, compiled in the browser by ``@babel/standalone``, and
rendered with React 18. It needs no ``component.py`` at all.

Structure:

.. code-block:: text

   _components/counter/
       component.djx
       component.css

``component.djx`` declares its dependencies via ``{% use_script %}`` right
at the top, then renders the mount point. The Babel payload is split into
**two** ``{% #use_script %}`` blocks so the content-based dedup can do its
job per-chunk: the first block carries the shared ``Counter`` component
definition (no per-instance context, so every render produces byte-identical
HTML and collapses to a single entry), while the second block mounts one
specific instance via ``{{ id }}`` (different bytes per render, so each
instance gets its own boot line):

.. code-block:: django

   {% use_script "https://unpkg.com/react@18.3.1/umd/react.production.min.js" %}
   {% use_script "https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js" %}
   {% use_script "https://unpkg.com/@babel/standalone@7.24.7/babel.min.js" %}

   <div class="counter-root" id="counter-{{ id }}" data-counter-label="{{ label }}"></div>

   {% #use_script %}
   <script type="text/babel" data-presets="react">
       const { useState } = React;
       window.Counter = function Counter({ label }) {
           const [count, setCount] = useState(0);
           return (
               <button type="button" className="btn btn-outline-success"
                       onClick={() => setCount(count + 1)}>
                   {label}: {count}
               </button>
           );
       };
   </script>
   {% /use_script %}

   {% #use_script %}
   <script type="text/babel" data-presets="react">
       {
           const mount = document.getElementById("counter-{{ id }}");
           ReactDOM.createRoot(mount).render(
               <Counter label={mount.dataset.counterLabel} />,
           );
       }
   </script>
   {% /use_script %}

.. note::

   A few browser-scope caveats when splitting a Babel payload across
   ``{% #use_script %}`` blocks:

   - **Cross-block sharing.** Each ``<script type="text/babel">`` tag is
     compiled and executed by Babel Standalone in its own wrapper, so
     top-level ``function Counter`` / ``const Counter`` declarations in
     the first block are not visible to the second one. Attaching the
     component to ``window`` (``window.Counter = function Counter(...)``)
     promotes it to global scope, which the JSX transformer resolves
     when it rewrites ``<Counter ... />`` into
     ``React.createElement(Counter, ...)`` inside the mount block.
   - **Avoid** ``data-type="module"``. It turns each block into an ES
     module with its own fully isolated scope, which defeats the
     ``window.Counter`` handshake. Plain ``<script type="text/babel">``
     executes in global scope after Babel rewrites it to classic JS.
   - **Wrap mount bodies in a block statement.** Without
     ``data-type="module"`` every mount script shares the global scope,
     so two ``const mount = ...`` lines (one per instance) would
     redeclare the same variable and throw
     ``SyntaxError: Can't create duplicate variable``. Wrapping the
     mount body in ``{ ... }`` gives ``const``/``let`` a local block
     scope while keeping ``Counter`` accessible from ``window``.
   - **Production builds.** For production prefer a pre-bundled
     ``component.js`` so the browser never sees ``@babel/standalone``
     at all. The block-form ``{% #use_script %}`` is most useful during
     development and for demos.

Render the counter twice on a page. The three CDN URLs are emitted
**once** (URL dedup), the shared ``Counter`` definition is emitted **once**
(content dedup, both renders produce the same body), and each mount gets
its own ``<script type="text/babel">`` body because ``{{ id }}`` interpolates
differently per render (``counter-likes`` vs ``counter-stars``). Rendering
the counter twice thus produces **three** Babel blocks in the scripts slot.
One shared definition plus two per-instance mounts, in render order:

.. code-block:: django

   {% component "counter" id="likes" label="Likes" %}
   {% component "counter" id="stars" label="Stars" %}

No build tool, no bundler, no copy-paste of CDN URLs in the base layout.
The component owns its dependencies directly from its template, and the
collector handles dedup and ordering.

.. tip::

   Because ``use_script`` prepends, declaring React / ReactDOM / Babel
   inside the component puts them at the top of the final ``<script>`` list
   in registration order. A layout-level ``use_script`` for something like
   Bootstrap JS still comes first, since it was registered earlier during
   render.

.. _static-dedup:

Deduplication
-------------

The collector uses two dedup strategies depending on the asset shape:

**URL-form assets** (co-located files, module-level ``styles`` / ``scripts``
lists, ``{% use_style %}`` / ``{% use_script %}`` declarations) dedupe by
URL. A second ``add()`` for the same URL is silently dropped, so:

- Rendering the same component N times on a page emits each of its CDN URLs
  exactly once.
- Declaring the same library in multiple places (layout ``use_style``, a page
  ``styles`` list, and a component ``styles`` list) produces a single
  ``<link>``. The first occurrence wins the slot.
- Cascading between layers keeps working: dedup matches on the final URL
  string, not on where the URL was registered.

**Inline block assets** (``{% #use_style %}`` / ``{% #use_script %}``)
dedupe by **rendered body content**. The rendered HTML itself becomes the
dedup key (scoped per kind, so the same body could theoretically appear in
both styles and scripts), so:

- A block rendered twice with the same template context ends up in the slot
  once.
- A block that interpolates variable context (``{{ id }}``, ``{{ label }}``)
  differs between renders and lands in the slot once per unique body. For
  example, the counter component's Babel block contains
  ``document.getElementById("counter-{{ id }}")`` so two mounts with
  ``id=likes`` and ``id=stars`` produce two distinct ``<script>`` tags.
- An author can intentionally write a "shared boot" block that does not
  reference any context variable, so all component instances converge on
  the same body and the boot script ships once.

.. tip::

   URL dedup is strictly by URL string. Two different URLs for the same
   library (``…/bootstrap.min.css`` vs ``…/bootstrap.css``) are treated as
   distinct assets and both will be included. Pick one canonical URL per
   dependency and stick to it across the project.

.. tip::

   Inline dedup is strictly by the rendered body. Whitespace and attribute
   order matter: ``<script src="a.js"></script>`` and
   ``<script src="a.js" ></script>`` are considered different bodies. If
   you want two components to share a single boot script, make sure the
   block bodies render to the exact same bytes.

Tips and gotchas
----------------

- **Slots are mandatory.** If a layout does not include ``{% collect_styles %}``
  or ``{% collect_scripts %}``, the matching assets are never inserted into
  the page. Add them once at the root layout and you are done.
- **Placeholders are HTML comments.** ``<!-- next:styles -->`` and
  ``<!-- next:scripts -->`` are valid HTML, so a half-rendered page (rare,
  e.g. when a custom view bypasses ``Page.render``) still parses correctly.
- **Two dedup strategies.** URL-form assets dedupe by URL. Inline blocks
  dedupe by rendered body. See the :ref:`deduplication <static-dedup>`
  section above. Two assets that resolve to different URLs (e.g. the same
  library at two different versions) are both included. Stick to one
  canonical URL per dependency.
- **``use_*`` lands at the top of the slot.** ``{% use_style %}`` and
  ``{% use_script %}`` are treated as shared dependencies and always appear
  before co-located files and module-level lists, regardless of where the tag
  is placed in the template. Multiple ``use_*`` calls keep their relative
  registration order. A ``use_style`` placed after the ``collect_styles``
  slot still works (the slot is rewritten **after** rendering), but for
  clarity keep your ``use_*`` calls near the top of the layout where the
  dependencies are conceptually declared.
- **Static URLs change with the route.** ``next/<route>.css`` is
  stable as long as the page route does not change. Renaming
  ``pages/blog/`` to ``pages/articles/`` will change ``template.css`` from
  ``next/blog.css`` to ``next/articles.css`` before manifest hashing.
- **Unified with Django staticfiles.** ``next.dj`` co-located assets and
  your regular ``{% static %}`` assets share the same ``collectstatic``
  pipeline and the same storage backend.

.. _static-custom-backends:

Custom backends
---------------

A backend is any class that subclasses :class:`~next.static.StaticBackend`.
The contract is small (three methods) and lets you swap how assets are
rendered and how URLs are produced (typically still via Django staticfiles).

.. code-block:: python

   from typing import Any

   from next.static import StaticBackend


   class CDNStaticBackend(StaticBackend):
       """Push co-located files to a CDN and return public URLs."""

       def __init__(self, config: dict[str, Any] | None = None) -> None:
           cfg = config or {}
           opts = cfg.get("OPTIONS") or {}
           self._base = opts["base_url"].rstrip("/")

       def register_file(self, source_path, logical_name, kind):
           extension = ".css" if kind == "css" else ".js"
           upload_to_cdn(source_path)
           return f"{self._base}/{logical_name}{extension}"

       def render_link_tag(self, url: str) -> str:
           return f'<link rel="stylesheet" href="{url}" crossorigin>'

       def render_script_tag(self, url: str) -> str:
           return f'<script src="{url}" defer></script>'

Wire it up like any other backend:

.. code-block:: python

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "myapp.backends.CDNStaticBackend",
               "OPTIONS": {"base_url": "https://cdn.example.com/static"},
           },
       ],
   }

Full walkthrough: a backend that adds SRI and ``defer``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The built-in :class:`~next.static.StaticFilesBackend` already honors
``css_tag`` / ``js_tag`` format strings, but programmatic tag construction
unlocks behavior driven by runtime state. Examples include
subresource-integrity hashes, conditional ``defer``, or per-URL
``crossorigin``. The example below subclasses the default backend and
extends ``render_script_tag``:

.. code-block:: python

   from collections.abc import Mapping
   from typing import Any

   from next.static import StaticFilesBackend


   class AttributedStaticFilesBackend(StaticFilesBackend):
       """Adds ``defer``, ``crossorigin`` and per-URL integrity hashes."""

       def __init__(self, config: Mapping[str, Any] | None = None) -> None:
           super().__init__(config)
           opts = dict((config or {}).get("OPTIONS") or {})
           self._defer = bool(opts.get("defer", False))
           self._crossorigin = opts.get("crossorigin") or None
           integrity = opts.get("integrity") or {}
           self._integrity = {str(k): str(v) for k, v in integrity.items()}

       def render_script_tag(self, url: str) -> str:
           attrs = [f'src="{url}"']
           if self._defer:
               attrs.append("defer")
           if self._crossorigin is not None:
               attrs.append(f'crossorigin="{self._crossorigin}"')
           sri = self._integrity.get(url)
           if sri is not None:
               attrs.append(f'integrity="{sri}"')
           return f"<script {' '.join(attrs)}></script>"

Settings:

.. code-block:: python

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "myapp.custom_backend.AttributedStaticFilesBackend",
               "OPTIONS": {
                   "defer": True,
                   "crossorigin": "anonymous",
                   "integrity": {
                       "https://cdn.example.com/app.js": "sha384-…",
                   },
               },
           },
       ],
   }

A complete working copy lives in
``examples/static/myapp/custom_backend.py``. The same example demonstrates
extending :meth:`~next.static.StaticBackend.render_link_tag` through the
parent's ``css_tag`` OPTIONS string.

.. _static-signals:

Signals reference
-----------------

:mod:`next.static.signals` exposes four Django signals that fire along the
static pipeline. They are synchronous by default (like Django core signals)
and let you hook into discovery, collector finalization, and HTML injection
without subclassing any of the static classes.

.. list-table::
   :header-rows: 1
   :widths: 24 22 54

   * - Signal
     - Sender
     - Keyword arguments
   * - :data:`~next.static.signals.asset_registered`
     - :class:`~next.static.StaticAsset`
     - ``collector`` (:class:`~next.static.StaticCollector`),
       ``backend`` (:class:`~next.static.StaticBackend`).
   * - :data:`~next.static.signals.collector_finalized`
     - :class:`~next.static.StaticCollector`
     - ``page_path`` (``Path`` or ``None``). Route being rendered.
   * - :data:`~next.static.signals.html_injected`
     - :class:`~next.static.StaticManager`
     - ``html_before`` (``str``), ``html_after`` (``str``),
       ``collector`` (:class:`~next.static.StaticCollector`).
   * - :data:`~next.static.signals.backend_loaded`
     - Concrete backend class (subclass of
       :class:`~next.static.StaticBackend`)
     - ``config`` (``dict``), ``instance``
       (:class:`~next.static.StaticBackend`).

Typical patterns:

.. code-block:: python

   from django.dispatch import receiver

   from next.static import StaticAsset
   from next.static.signals import asset_registered, collector_finalized


   @receiver(asset_registered, sender=StaticAsset)
   def log_asset(sender, *, asset, collector, backend, **_):
       print("registered", asset.url, "via", type(backend).__name__)


   @receiver(collector_finalized)
   def count_assets(sender, *, page_path, **_):
       print(page_path, len(sender))

Guidelines:

- Signal receivers run synchronously inside the render pipeline. Keep them
  cheap and side-effect-free.
- Do not call :meth:`~next.static.StaticCollector.add` from inside an
  ``asset_registered`` receiver. The collector already has re-entrancy
  protection but a self-triggered chain will simply be ignored. It is
  clearer to append the extra asset on the caller side.
- :data:`~next.static.signals.backend_loaded` fires once per backend
  construction. With the default configuration that happens on application
  startup (and again on every ``setting_changed`` reset). Do not assume it
  runs on every request.

Troubleshooting
---------------

**``<!-- next:styles -->`` / ``<!-- next:scripts -->`` leaks into the HTML**
    The layout is missing a ``{% collect_styles %}`` or
    ``{% collect_scripts %}`` tag, or the surrounding view bypassed
    :meth:`next.pages.Page.render` (which triggers the inject phase). Add
    the missing tag or run the render through a page view.

**``RuntimeError: Static asset 'next/...' is missing from Django staticfiles manifest``**
    :class:`~django.contrib.staticfiles.storage.ManifestStaticFilesStorage`
    cannot find a hashed entry for the requested co-located asset. Run
    ``python manage.py collectstatic`` and make sure
    ``NextStaticFilesFinder`` is in ``STATICFILES_FINDERS`` (the framework
    adds it automatically via :mod:`next.apps`).

**Assets listed by ``collectstatic`` but browser 404s**
    The file is picked up by the finder but not served under the expected
    URL. Double-check ``STATIC_URL`` is ``/static/`` (or matches your
    deployment) and that production traffic actually hits
    ``STATIC_ROOT``. In development, ``django.contrib.staticfiles`` must be
    in ``INSTALLED_APPS``.

**``TypeError: Backend '…' is not a StaticBackend subclass``**
    :class:`~next.static.StaticsFactory` refuses configurations whose
    ``BACKEND`` dotted path does not resolve to a
    :class:`~next.static.StaticBackend` subclass. Check the import path
    and that the class inherits from the ABC.

**System check ``next.E036`` / ``next.E037`` / ``next.E038`` / ``next.W030``**
    See :mod:`next.static.checks`. The messages explain the exact shape
    mismatch in ``NEXT_FRAMEWORK['DEFAULT_STATIC_BACKENDS']``.

**``{% #use_script %}`` body rendered into the wrong slot**
    Block forms always hoist into the matching ``collect_*`` slot at the
    end of rendering. If the slot is missing from the layout, the content
    is dropped. Add the slot and the block will land in place.

**Two identical inline blocks ship twice**
    Inline dedup is byte-exact. Whitespace, attribute order, and
    context-interpolated values all contribute to the dedup key. Factor
    the shared part into a context-free block and keep per-instance logic
    in a second block. See the counter example in ``examples/static/``
    for the pattern.

**Accessing the manager in tests or at runtime**
    Import :data:`~next.static.default_manager`. It is a
    :class:`~django.utils.functional.LazyObject` that resets automatically
    when ``NEXT_FRAMEWORK`` changes. In tests, call
    :func:`~next.static.reset_default_manager` between overrides.

Public API
----------

The full public surface lives in ``next.static`` (see :ref:`api-reference`).
The most useful entry points:

- :class:`~next.static.StaticAsset`. Frozen dataclass for one CSS or JS
  reference.
- :class:`~next.static.StaticCollector`. Per-render dedup and order helper.
- :class:`~next.static.StaticBackend`. Backend ABC. Subclass it for custom
  hosting.
- :class:`~next.static.StaticFilesBackend`. Built-in backend that resolves
  co-located assets through Django ``staticfiles_storage``.
- :class:`~next.static.StaticsFactory`. Creates backend instances from the
  ``DEFAULT_STATIC_BACKENDS`` configuration.
- :class:`~next.static.AssetDiscovery`. Scans page, layout, and component
  directories and module-level lists.
- :class:`~next.static.StaticManager`. Coordinates backends, discovery, and
  placeholder injection.
- :class:`~next.static.KindRegistry` and :data:`~next.static.default_kinds`.
  Register extra asset kinds beyond the built-in CSS and JS.
- :class:`~next.static.NextScriptBuilder` and
  :class:`~next.static.ScriptInjectionPolicy`. Control how ``next.min.js``
  is injected.
- :data:`~next.static.default_manager`. Lazy module-level handle used by
  :meth:`~next.pages.Page.render` and the static template tags. Replace the
  wrapped instance in tests by assigning to ``default_manager._wrapped``.
  Call :func:`~next.static.reset_default_manager` to drop it entirely.
- :class:`~next.static.NextStaticFilesFinder`. Staticfiles finder that exposes
  co-located assets under the ``next/`` namespace for ``collectstatic``.
- :mod:`next.static.signals`. Signals emitted across the pipeline.

.. _next-object:

The Next object and JavaScript context
---------------------------------------

next.dj automatically injects a global ``Next`` class on every rendered page.
No template changes are required. The class is wired in by the inject phase
alongside the rest of the static pipeline.

JavaScript code running on the page can read Python-side context values
through ``window.Next.context``. The object is frozen and immutable from
script scope.

How it is injected
~~~~~~~~~~~~~~~~~~

When ``{% collect_scripts %}`` is processed, the inject phase unconditionally
prepends two tags ahead of all user-level scripts:

.. code-block:: html

   <script src="/static/next/next.min.js"></script>
   <script>Next._init({"page": "home", "theme": "dark"});</script>

The ``next.min.js`` tag is a plain synchronous ``<script>``, no ``defer`` or
``async``, so the ``Next`` class is fully defined by the time ``Next._init``
runs. As a further optimisation, a ``<link rel="preload" as="script">`` hint
for the same file is inserted immediately before ``</head>``:

.. code-block:: html

   <head>
       ...
       <link rel="preload" as="script" href="/static/next/next.min.js">
   </head>

This gives the browser an early download signal while the rest of the page
continues to render.

Exposing Python values via ``serialize=True``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default context functions only populate the Django template context. To
also expose a value to JavaScript, add ``serialize=True`` to the decorator:

**Page context** (``page.py``):

.. code-block:: python

   from next.pages import context

   @context("page_meta", serialize=True)
   def get_page_meta() -> dict:
       return {"page": "home", "version": "1.0"}

**Component context** (``component.py``):

.. code-block:: python

   from next.components import context

   @context("theme", serialize=True)
   def get_theme() -> str:
       return "dark"

Both forms accept any JSON-serialisable value such as dicts, lists, strings,
numbers, or booleans. Values are serialised with ``DjangoJSONEncoder``, which
handles ``datetime``, ``Decimal``, ``UUID``, and Django lazy strings in
addition to the standard JSON types.

Unkeyed context functions (decorated with ``@context`` without a key) merge
every key from the returned dict into the JavaScript context individually.

The result in the rendered HTML:

.. code-block:: html

   <script>Next._init({"page_meta":{"page":"home","version":"1.0"},"theme":"dark"});</script>

Reading context in JavaScript
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``window.Next.context`` returns a frozen copy of the serialised context. It
is synchronously available by the time any ``{% #use_script %}`` body or
co-located ``component.js`` / ``template.js`` runs because ``next.min.js``
executes before all user scripts.

**Vanilla JS:**

.. code-block:: javascript

   const { page_meta, theme } = window.Next.context;
   console.log(page_meta.page, theme); // "home" "dark"

**React** (inside a ``{% #use_script %}`` block):

.. code-block:: jsx

   function PageBadge() {
       const ctx = window.Next.context;
       return React.createElement("span", null, ctx.page_meta?.page || "");
   }
   ReactDOM.createRoot(document.getElementById("badge")).render(
       React.createElement(PageBadge)
   );

No ``useEffect`` or lifecycle hook is needed. The data is available
synchronously before your scripts run.

**TypeScript**. Declare the global so the compiler is happy:

.. code-block:: typescript

   declare const Next: { context: Readonly<Record<string, unknown>> };

Key conflict resolution
~~~~~~~~~~~~~~~~~~~~~~~

When a page context function and a component context function both register
the same key, **the first registration wins**. Page context functions are
collected before component context functions, so page values always take
priority over component values for the same key:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Registration order
     - Effect
   * - Page ``@context(serialize=True)`` first
     - Page value is kept. Component value for the same key is silently dropped.
   * - Component ``@context(serialize=True)`` first
     - Component value is kept. A later page value for the same key is dropped.

In practice, page context is always resolved before any component context on
the same page, so page values reliably win whenever both layers use the same
key.

.. tip::

   Use distinct key names (or nest values under a prefixed dict) whenever a
   page and one of its components both need to publish JavaScript state. That
   avoids the first-wins rule entirely and keeps each layer's data
   self-contained.

Example project
---------------

The ``examples/static/`` project shows the complete picture: a root layout
with shared Bootstrap from ``{% use_style %}`` / ``{% use_script %}``, a home
page with co-located ``template.css`` plus an Inter-font in ``page.py``, a
dashboard page with its own ``template.css`` and JetBrains Mono font, two
composite components (``widget`` with Bootstrap Icons and ``chart`` with
Chart.js), a ``next-demo`` component that reads ``window.Next.context`` in a
plain ``<script>`` block, and a full test suite that exercises collector
ordering, deduplication, staticfiles-based URL resolution, and JavaScript
context injection.
