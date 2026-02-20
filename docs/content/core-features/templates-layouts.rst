Templates & Layouts
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

Template Loading Priority
-------------------------

The system tries to load templates in this order:

1. **Layout templates** (``layout.djx`` files) - highest priority
2. **Python string templates** (``template`` attribute)
3. **Template files** (``template.djx`` files)

This allows for flexible template organization and inheritance.

Layout System
-------------

The layout system uses ``layout.djx`` files to create hierarchical template inheritance. Unlike Django's template inheritance, this system works through **file-based composition** rather than extends/block syntax.

How Layout Inheritance Works
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Layout Discovery**: The system scans parent directories for ``layout.djx`` files
2. **Template Wrapping**: Page templates are wrapped in ``{% block template %}{% endblock %}``
3. **Hierarchical Composition**: Layouts are composed by string replacement, not Django's extends mechanism
4. **Block Replacement**: The ``{% block template %}{% endblock %}`` in each layout is replaced with the composed content

Multiple NEXT_PAGES Configurations
----------------------------------

next.dj supports multiple NEXT_PAGES configurations, allowing you to have different layout hierarchies for different parts of your application:

.. code-block:: python

   NEXT_PAGES = [
       {
           'BACKEND': 'next.urls.FileRouterBackend',
           'APP_DIRS': True,
           'OPTIONS': {
               'pages_dir': 'pages',  # Main site pages
           },
       },
       {
           'BACKEND': 'next.urls.FileRouterBackend',
           'APP_DIRS': True,
           'OPTIONS': {
               'pages_dir': 'admin_pages',  # Admin interface pages
           },
       },
   ]

This allows you to have:
- **Main site layout** in ``pages/layout.djx``
- **Admin layout** in ``admin_pages/layout.djx``
- **Different styling and structure** for different sections

Root Layout for Entire Site
~~~~~~~~~~~~~~~~~~~~~~~~~~~

A site-wide layout is provided the same way as root-level pages: via **PAGES_DIRS** or **PAGES_DIR** in the same backend (see :doc:`file-router`). Add a directory (e.g. ``root_pages``) that contains ``layout.djx``; it can contain only that file (no ``page.py``) and will be used as an additional layout for all app pages. If the directory also has its own pages, they are served as root-level URLs; duplicate URL patterns with app pages cause a check error (``next.E015``).

.. code-block:: python

   OPTIONS: {
       "PAGES_DIRS": [str(BASE_DIR / "root_pages")],
   }

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
2. **Additional layouts** (from other NEXT_PAGES directories)

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
------------------------

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
- Can be disabled by setting ``NEXT_PAGES_OPTIONS.check_layout_template_blocks = False``

**Missing Template Validation** (``check_missing_templates``):
- Ensures every page.py has either a template attribute or template.djx file
- Prevents pages from being created without proper template definitions

**Missing Page Content Validation** (``check_missing_page_content``):
- Checks for page.py files that have no content (no template, no render function)
- Validates that pages have either template variable, template.djx file, layout.djx file, or render function
- Can be disabled by setting ``NEXT_PAGES_OPTIONS.check_missing_page_content = False``

Run validation checks:

.. code-block:: bash

   python manage.py check

Examples
--------

See the ``examples/`` directory in the source code for complete working examples:

- **layouts/**: Layout inheritance examples with Bootstrap styling
- **pages/**: Template usage examples

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
