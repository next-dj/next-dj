.. _topics-layouts:

Layouts
=======

A layout is a ``layout.djx`` file in any directory under a page root.
The framework wraps every page below it through string substitution into a ``{% block template %}`` placeholder.
This page covers how layouts are discovered, how the layout chain composes, how to publish layout-level context, and how multiple page backends produce independent layout trees.

.. contents::
   :local:
   :depth: 2

Overview
--------

A page is wrapped by every ``layout.djx`` between the page directory and the page root.
The closest layout is the innermost wrapper.
The root layout is the outermost wrapper.
The page body is substituted into a ``{% block template %}{% endblock template %}`` placeholder that each layout must contain.

Layouts compose through string substitution, not through Django template inheritance.
There is no ``{% extends %}`` directive and no parent template identifier.
The composition is purely structural, driven by directory placement.

Layout Discovery
----------------

The framework walks from the page directory upward until it reaches the page root.
Every directory along the way is inspected for a ``layout.djx`` file.
The collected layouts are composed in order, with the closest layout wrapping the page body and the farthest layout wrapping everything else.

.. code-block:: text
   :caption: layout discovery

   notes/routes/
     layout.djx                outer wrapper for every page
     notes/
       layout.djx              inner wrapper for /notes/* pages
       [id]/
         layout.djx            innermost wrapper for /notes/<id>/* pages
         template.djx
         page.py

A request to ``/notes/42/`` composes three layouts.
The innermost wraps the page body.
The middle layout wraps the result.
The outermost layout wraps the result again.

Layout Template Contract
------------------------

Every layout must contain a ``{% block template %}{% endblock template %}`` placeholder where the body of the wrapped content is substituted.

.. code-block:: jinja
   :caption: notes/routes/layout.djx

   <!doctype html>
   <html>
     <head>
       <title>{{ site_name }}</title>
       {% collect_styles %}
     </head>
     <body>
       <header>{{ site_name }}</header>
       <main>
         {% block template %}{% endblock template %}
       </main>
       {% collect_scripts %}
     </body>
   </html>

Without the placeholder the layout still renders, but the page body is dropped silently.
Run ``uv run python manage.py check`` to catch layouts that miss the placeholder.

Layouts can declare layout-level CSS and JS through the static collector tags shown above.
The tags also live in inner layouts when you want a section-scoped style sheet.

Publishing Context From a Layout
--------------------------------

A layout can have its own ``layout.py`` next to ``layout.djx``.
Use it to publish values for the layout markup and, when needed, for every descendant page.

.. code-block:: python
   :caption: notes/routes/layout.py

   from notes.models import Note

   from next.pages import context


   @context("site_name", inherit_context=True)
   def site_name() -> str:
       return "Notes"


   @context("note_count", inherit_context=True)
   def note_count() -> int:
       return Note.objects.count()

The ``inherit_context=True`` flag publishes the value to every descendant page.
Without the flag the value is only visible to the layout markup itself, not to the pages below.

Layout context functions can take dependencies the same way pages do.
The resolver shares its cache across the layout chain and the page so a value resolved in a layout is not recomputed in the page.

Nested Layout Patterns
----------------------

Section Layout
~~~~~~~~~~~~~~

Add a layout inside a section to share a sub navigation across every page under that section.

.. code-block:: jinja
   :caption: notes/routes/admin/layout.djx

   <section class="admin">
     <nav class="admin-nav">
       <a href="{% url 'next:page_admin' %}">Overview</a>
       <a href="{% url 'next:page_admin_stats' %}">Stats</a>
     </nav>
     {% block template %}{% endblock template %}
   </section>

Empty Pass Through
~~~~~~~~~~~~~~~~~~

Sometimes a directory needs a Python-level layout for context but no extra HTML.
Use an empty layout that contains only the placeholder.

.. code-block:: jinja
   :caption: notes/routes/api/layout.djx

   {% block template %}{% endblock template %}

The accompanying ``layout.py`` can still publish context for every page under ``/api/`` without injecting any markup.

Section Specific Static Assets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Co-locate ``layout.css`` next to ``layout.djx`` to ship styles that apply only to pages under that directory.
The static collector emits the file only when a request reaches a page below that layout.

Multiple Backends and Layout Roots
----------------------------------

Each entry in ``DEFAULT_PAGE_BACKENDS`` produces an independent layout tree.
Two backends can have entirely different root layouts even when both scan the same applications.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "PAGES_DIR": "routes",
           },
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "PAGES_DIR": "admin_routes",
           }
       ]
   }

The first backend reads ``notes/routes/`` and uses ``notes/routes/layout.djx`` as its root.
The second backend reads ``notes/admin_routes/`` and uses ``notes/admin_routes/layout.djx`` as its root.
The two trees do not share layouts.

Project Level Root Layout
-------------------------

Place a root layout outside of any application by adding a project directory to ``DIRS``.

.. code-block:: text
   :caption: project layout

   chrome/
     layout.djx
   notes/
     routes/
       page.py
       template.djx

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "DIRS": [str(BASE_DIR / "chrome")],
               "PAGES_DIR": "routes",
           }
       ]
   }

The router walks application directories first then continues into ``chrome``.
The ``chrome/layout.djx`` wraps every page found in every application.
See :doc:`multi-project` for the full pattern.

Cross Cutting Behaviour
-----------------------

Context Processors
~~~~~~~~~~~~~~~~~~

Context processors configured on a backend run before the layout chain renders.
They contribute variables that every layout and every page can use.

Static Collector
~~~~~~~~~~~~~~~~

The static collector runs once per request.
Both layouts and the page contribute to the same collection slot.
A ``{% collect_styles %}`` tag inside an inner layout still emits every collected stylesheet, not only the ones declared in that layout.

Page Rendered Signal
~~~~~~~~~~~~~~~~~~~~

The ``page_rendered`` signal fires once per request after the layout chain produces the final HTML.
Subscribe to observe the rendered body without disturbing the rendering pipeline.

Common Pitfalls
---------------

Layout renders but page body is missing.
   The ``{% block template %}{% endblock template %}`` placeholder is required.
   Without it the framework still renders the layout but the page content is dropped.

Inherited context not visible to a sub layout.
   Layouts share the same context flow as pages.
   The ``inherit_context=True`` flag is required when a layout consumes a value declared in an ancestor layout.

Two roots produce one composed page.
   Each backend has its own layout tree.
   A page lives under exactly one backend, even when the file path is also reachable from another backend.

See Also
--------

.. seealso::

   :doc:`pages` for the page body sources and the layout placeholder.
   :doc:`context` for context inheritance rules.
   :doc:`multi-project` for project-level layout roots.
   :doc:`/content/internals/page-discovery` for the composition pipeline.
