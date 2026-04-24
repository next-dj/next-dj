Pages and Templates
===================

next.dj uses a custom template system with ``.djx`` files that provides powerful inheritance and composition capabilities. Unlike traditional Django templates, the inheritance system works through file-based layout composition rather than Django's extends mechanism.

Template Types
--------------

**Python String Templates**:
Define templates directly in your page files:

.. code-block:: python

   # pages/home/page.py
   template = """
   <!DOCTYPE html>
   <html>
   <head>
       <title>{{ page_title }}</title>
   </head>
   <body>
       <h1>{{ greeting }}</h1>
       <p>{{ content }}</p>
   </body>
   </html>
   """

**Template Files (.djx)**:
Use separate template files for better organization:

.. code-block:: python

   # pages/about/page.py
   @context
   def get_about_data(request):
       return {
           "company_name": "next.dj",
           "description": "A modern Django framework"
       }

.. code-block:: html

   <!-- pages/about/template.djx -->
   <!DOCTYPE html>
   <html>
   <head>
       <title>About {{ company_name }}</title>
   </head>
   <body>
       <h1>About {{ company_name }}</h1>
       <p>{{ description }}</p>
   </body>
   </html>

Page body sources and priority
------------------------------

A page supplies its body through one of three mechanisms. All three are composed through the ancestor ``layout.djx`` chain the same way, and the framework runs the context processors, ``StaticCollector``, and ``page_rendered`` signal exactly once per request regardless of which source produced the body.

Priority (highest wins):

1. ``render(request, ...)`` **function** on the page module.
2. ``template = "..."`` **module attribute** (a plain string).
3. Sibling ``template.djx`` file.

When more than one source is declared on the same ``page.py``, the lower-priority ones are silently dropped at render time, and ``manage.py check`` emits a :ref:`next.W043 <check-next-w043>` warning pointing at the file.

Render functions
~~~~~~~~~~~~~~~~

A ``render`` function receives DI-resolved arguments (request, URL kwargs, named dependencies) and decides what the page returns:

.. code-block:: python

   # screens/report/page.py
   from django.http import HttpResponse, HttpResponseRedirect
   from next.pages import context

   @context("today")
   def _today() -> str:
       return "2026-04-25"

   def render(request, today: str) -> str:
       # String body — composed through the ancestor layout chain.
       return f"<section>Report for {today}</section>"

   def redirect(request) -> HttpResponseRedirect:
       # Any HttpResponse subclass bypasses the layout and static pipeline.
       return HttpResponseRedirect("/")

Return-type contract:

* ``str`` — treated as the page body. Layout composition, context processors, ``StaticCollector``, and ``page_rendered`` run as for ``template.djx`` and ``template``.
* Any :class:`django.http.HttpResponse` (including :class:`~django.http.HttpResponseRedirect`, :class:`~django.http.StreamingHttpResponse`, :class:`~django.http.JsonResponse`, and your own subclasses) — returned verbatim. Layout and static pipeline are skipped. This is the escape hatch for redirects, JSON APIs, streaming, and anything else that is not a server-rendered HTML page.
* Anything else (``None``, ``dict``, ``list``, …) — raises ``TypeError`` naming the file so mistakes surface immediately.

Exceptions raised inside ``render()`` propagate to Django's request-handling stack unchanged.

.. note::

   ``Page.render`` (the programmatic API exposed to tests and tools) uses the static body sources only; it never invokes ``render()``. The unified view function that handles real HTTP requests invokes ``render()`` first and hands its string result to the same composition pipeline.

Custom template formats
~~~~~~~~~~~~~~~~~~~~~~~

The sibling ``template.djx`` file is just one implementation of the ``TemplateLoader`` contract. Register additional loaders under ``NEXT_FRAMEWORK["TEMPLATE_LOADERS"]`` to teach the framework about any file format you like. Each entry is a dotted path to a ``next.pages.loaders.TemplateLoader`` subclass. Loaders are consulted in the order they appear after the ``template`` module attribute is checked.

.. code-block:: python

   # myapp/loaders.py
   from pathlib import Path
   import markdown
   from next.pages.loaders import TemplateLoader

   class MarkdownTemplateLoader(TemplateLoader):
       source_name = "template.md"

       def can_load(self, file_path: Path) -> bool:
           return (file_path.parent / "template.md").exists()

       def load_template(self, file_path: Path) -> str | None:
           md = (file_path.parent / "template.md").read_text(encoding="utf-8")
           return markdown.markdown(md, extensions=["fenced_code"])

       def source_path(self, file_path: Path) -> Path | None:
           p = file_path.parent / "template.md"
           return p if p.exists() else None

.. code-block:: python

   # settings.py
   NEXT_FRAMEWORK = {
       "TEMPLATE_LOADERS": [
           "myapp.loaders.MarkdownTemplateLoader",
           "next.pages.loaders.DjxTemplateLoader",
       ],
   }

User-provided ``TEMPLATE_LOADERS`` **replaces** the default list (which is just ``DjxTemplateLoader``), so include ``DjxTemplateLoader`` explicitly if you still want sibling ``template.djx`` support. ``source_name`` is used by :ref:`next.W043 <check-next-w043>` to name the active source in conflict warnings; ``source_path`` feeds the stale-cache detector so edits to the backing file invalidate the composed template on the next request.

Layout System
-------------

The layout system uses ``layout.djx`` files to create hierarchical template inheritance. Unlike Django's template inheritance, this system works through **file-based composition** rather than extends/block syntax.

How Layout Inheritance Works
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Layout Discovery**: The system scans parent directories for ``layout.djx`` files
2. **Template Wrapping**: Page templates are wrapped in ``{% block template %}{% endblock %}``
3. **Hierarchical Composition**: Layouts are composed by string replacement, not Django's extends mechanism
4. **Block Replacement**: The ``{% block template %}{% endblock %}`` in each layout is replaced with the composed content

Multiple router entries
-----------------------

next.dj supports multiple entries in ``NEXT_FRAMEWORK["DEFAULT_PAGE_BACKENDS"]``, allowing you to have different layout hierarchies for different parts of your application:

.. code-block:: python

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "PAGES_DIR": "pages",
               "APP_DIRS": True,
               "DIRS": [],
               "OPTIONS": {"context_processors": []},
           },
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "PAGES_DIR": "admin_pages",
               "APP_DIRS": True,
               "DIRS": [],
               "OPTIONS": {"context_processors": []},
           },
       ],
   }

This allows you to have:
- **Main site layout** in ``pages/layout.djx``
- **Admin layout** in ``admin_pages/layout.djx``
- **Different styling and structure** for different sections

Root Layout for Entire Site
~~~~~~~~~~~~~~~~~~~~~~~~~~~

A site-wide layout is provided the same way as root-level pages: add path roots to **``DIRS``** on the file router backend (see :doc:`file-router`). The **``PAGES_DIR``** key is only the name of the app subdirectory (e.g. ``"pages"``), not a list of project roots. Add a directory (e.g. ``root_pages``) that contains ``layout.djx``. It can contain only that file (no ``page.py``) and will be used as an additional layout for all app pages. If the directory also has its own pages, they are served as root-level URLs. Duplicate URL patterns with app pages cause a check error (``next.E015``).

.. code-block:: python

   "DIRS": [str(BASE_DIR / "root_pages")],

.. code-block:: html

   <!-- root_pages/layout.djx -->
   <!DOCTYPE html>
   <html lang="en">
   <head>
       <meta charset="UTF-8">
       <meta name="viewport" content="width=device-width, initial-scale=1.0">
       <title>{% block title %}My Site{% endblock %}</title>
       <link rel="stylesheet" href="/static/css/main.css">
   </head>
   <body>
       <header>
           <nav>
               <a href="/">Home</a>
               <a href="/about/">About</a>
               <a href="/contact/">Contact</a>
           </nav>
       </header>
       
       <main>
           {% block template %}{% endblock %}
       </main>
       
       <footer>
           <p>&copy; 2024 My Site</p>
       </footer>
   </body>
   </html>

This layout is automatically applied as the outermost wrapper for all pages that use it.

Layout Inheritance Chain
------------------------

Layouts can inherit from each other through the directory hierarchy. The system automatically discovers and composes layouts from multiple sources:

1. **Local layouts** (from current directory hierarchy)
2. **Additional layouts** (from other configured root pages directories)

When a page is rendered, the system composes all layouts in the inheritance chain:

1. **Root layout** (``pages/layout.djx``)
2. **Section layout** (``pages/blog/layout.djx``)  
3. **Subsection layout** (``pages/blog/post/layout.djx``)
4. **Page template** (``pages/blog/post/[slug]/template.djx``)

The final template is created by:
1. Wrapping the page template in ``{% block template %}{% endblock %}``
2. Replacing ``{% block template %}{% endblock %}`` in each layout with the composed content
3. Processing layouts from closest to furthest parent directory

Layout Context
--------------

Layouts can have their own context functions that are inherited by child pages:

.. code-block:: python

   # pages/layout/page.py
   @context("custom_variable", inherit_context=True)
   def custom_variable_context_with_inherit(request):
       return "This context is inherited by all child pages"

   @context("custom_variable_2")
   def custom_variable_2_context(request):
       return "This context is NOT inherited by child pages"

The ``inherit_context=True`` parameter makes the context available to all child pages using this layout.

Accessing parent or global context in child pages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In a child page's context functions you can inject:

- **Parent/layout context by key** — Use ``Context("key")`` to get a value that
  was set by the layout (e.g. ``@context("custom_variable", inherit_context=True)``)
  or by an earlier context function on the same page.

- **Layout-level global dependency** — Register a callable with
  ``@resolver.dependency("name")`` in the layout (or app) and inject it in child
  pages with ``Depends("name")``.

Example:

.. code-block:: python

   # Layout: pages/page.py
   from next.deps import resolver
   from next.pages import context

   @resolver.dependency("layout_theme")
   def get_layout_theme():
       return {"name": "Bootstrap", "version": "5.0"}

   @context("custom_variable", inherit_context=True)
   def custom_variable_context_with_inherit(request):
       return "Hello from layout!"

   # Child: pages/guides/page.py
   from next.deps import Depends
   from next.pages import Context, context

   @context("layout_theme_data")
   def guides_theme(layout_theme: dict[str, str] | None = Depends("layout_theme")):
       return layout_theme

   @context("parent_context_data")
   def guides_parent(custom_variable: str | None = Context("custom_variable")):
       return custom_variable

Then in ``guides/template.djx`` you can use ``{{ layout_theme_data }}`` and
``{{ parent_context_data }}``. For the full walkthrough, see
:doc:`dependency-injection` (Context vs Depends). The
``examples/feature-flags/flags/panels/admin/`` directory demonstrates
a nested layout with inherited context in a working project.

Layout Blocks
-------------

Use Django template blocks for flexible content areas:

.. code-block:: html

   <!-- pages/layout.djx -->
   <!DOCTYPE html>
   <html>
   <head>
       <title>{% block title %}My Site{% endblock %}</title>
       {% block extra_head %}{% endblock %}
   </head>
   <body>
       {% block header %}
       <header>
           <h1>My Site</h1>
       </header>
       {% endblock %}
       
       <main>
           {% block template %}{% endblock %}
       </main>
       
       {% block footer %}
       <footer>
           <p>&copy; 2024 My Site</p>
       </footer>
       {% endblock %}
   </body>
   </html>

Page templates can override these blocks:

.. code-block:: html

   <!-- pages/about/template.djx -->
   {% block title %}About Us - My Site{% endblock %}

   {% block extra_head %}
   <link rel="stylesheet" href="/static/css/about.css">
   {% endblock %}

   {% block template %}
   <h1>About Us</h1>
   <p>Learn more about our company.</p>
   {% endblock %}

Django Template Features
-------------------------

All templates support full Django template functionality:

**Variables**:

.. code-block:: html

   <h1>{{ title }}</h1>
   <p>{{ user.name|default:"Guest" }}</p>

**Filters**:

.. code-block:: html

   <p>{{ content|truncatewords:30 }}</p>
   <p>{{ date|date:"F j, Y" }}</p>

**Tags**:

.. code-block:: html

   {% if user.is_authenticated %}
       <p>Welcome, {{ user.username }}!</p>
   {% else %}
       <p>Please log in.</p>
   {% endif %}

   {% for item in items %}
       <li>{{ item.name }}</li>
   {% endfor %}

Template Caching
----------------

Templates are automatically cached for performance. The cache is cleared when:

1. Django development server restarts
2. Template files are modified
3. Page files are modified

Validation Checks
-----------------

The system includes validation checks for template integrity:

**Layout Template Validation** (``check_layout_templates``):
- Validates that layout.djx files contain the required ``{% block template %}`` structure
- Provides warnings for layout files that may cause inheritance issues

**Page modules** (``check_page_functions``):
- Ensures each ``page.py`` defines a callable ``render`` and/or a template (attribute or ``template.djx``)
- Emits a warning when a file has no template, render, ``template.djx``, or ``layout.djx`` (see :doc:`../reference/system-checks`)

Run validation checks:

.. code-block:: bash

   python manage.py check

Examples
--------

See the ``examples/`` directory in the source repository for complete
working projects:

- ``examples/_template`` — minimal page + layout + template scaffold.
- ``examples/markdown-blog`` — ``posts/<slug>/`` pages backed by a
  ``MarkdownTemplateLoader`` (registered via ``TEMPLATE_LOADERS``).
- ``examples/feature-flags`` — nested layouts under ``admin/`` with
  inherited context.

Best Practices
--------------

1. **Use template files for complex templates**: Easier to edit and maintain
2. **Leverage layout inheritance**: Create reusable page structures
3. **Use meaningful block names**: Makes inheritance clearer
4. **Provide sensible defaults**: Always include fallback content in layouts
5. **Test layout inheritance**: Ensure all combinations work correctly
6. **Keep templates simple**: Move complex logic to context functions
7. **Use Django template features**: Take advantage of filters, tags, and inheritance
8. **Plan your layout hierarchy**: Design clear inheritance chains
9. **Use multiple configurations**: Separate different sections of your application

Extension points
----------------

The pages subsystem exposes one pluggable surface for loading template source.

* ``next.pages.loaders.TemplateLoader`` is the abstract contract used by ``Page`` to read template source by path. The default stack resolves Python modules, ``.djx`` files, and layout chains. Subclass it to read sources from a database, a bundled archive, or an in-memory map.

The canonical registration surface is the ``TEMPLATE_LOADERS`` key in
``NEXT_FRAMEWORK`` (see above). A working example lives in
``examples/markdown-blog/blog/loaders.py`` — ``MarkdownTemplateLoader``
reads a sibling ``template.md`` and returns rendered HTML.

The signals emitted by :mod:`next.pages.signals` let external code observe the rendering pipeline.

* ``template_loaded`` fires when a loader returns source for a path.
  Kwargs: ``file_path``.
* ``context_registered`` fires when a context function is attached to a page.
  Kwargs: ``file_path``, ``key``.
* ``page_rendered`` fires after a page finishes rendering.
  Kwargs: ``file_path``, ``duration_ms``, ``styles_count``, ``scripts_count``,
  ``context_keys``.

See :doc:`extending` for the overall extension model.

Next
----

:doc:`context` — Passing data to templates with the context system.
