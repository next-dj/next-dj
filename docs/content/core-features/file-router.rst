File Router
===========

next.dj provides a powerful file-based routing system that automatically generates Django URL patterns from your file system structure. This eliminates the need to manually write URL configurations.

How It Works
------------

The file router scans your project for ``page.py`` files and automatically creates Django URL patterns based on the directory structure. Each directory becomes a URL segment, creating a hierarchical URL structure that mirrors your file system.

Basic File Structure
~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   pages/
   ├── home/
   │   └── page.py          → /home/
   ├── about/
   │   └── page.py          → /about/
   └── blog/
       ├── page.py          → /blog/
       └── post/
           └── [slug]/
               └── page.py  → /blog/post/<str:slug>/

URL Pattern Generation
----------------------

The system automatically converts file paths to Django URL patterns using a custom syntax:

**String Parameters** (default):

.. code-block:: text

   [username]               → <str:username>

**Typed Parameters**:

.. code-block:: text

   [int:post_id]           → <int:post_id>
   [slug:category]         → <slug:category>
   [uuid:user_id]          → <uuid:user_id>

**Wildcard Parameters**:

.. code-block:: text

   [[path]]                → <path:path>

**Examples**:

::

   pages/
   ├── user/
   │   └── [username]/
   │       └── page.py      → /user/<str:username>/
   ├── post/
   │   └── [int:post_id]/
   │       └── page.py      → /post/<int:post_id>/
   └── api/
       └── [[path]]/
           └── page.py      → /api/<path:path>/

Virtual Routes
--------------

Routes can exist without ``page.py`` files if they have a ``template.djx`` file:

.. code-block:: text

   pages/
   ├── static/
   │   └── about/
   │       └── template.djx  → /static/about/ (virtual route)
   └── api/
       └── health/
           └── template.djx  → /api/health/ (virtual route)

URL Naming
----------

URL patterns are automatically named based on the file path:

.. code-block:: text

   pages/home/page.py                    → page_home
   pages/user/[username]/page.py         → page_user_username
   pages/blog/post/[int:post_id]/page.py → page_blog_post_post_id

Use these names in your templates:

.. code-block:: html

   <a href="{% url 'page_home' %}">Home</a>
   <a href="{% url 'page_user_username' username='john' %}">John's Profile</a>

Configuration
-------------

Configure the file router in your Django settings:

.. code-block:: python

   NEXT_PAGES = [
       {
           'BACKEND': 'next.urls.FileRouterBackend',
           'APP_DIRS': True,  # Scan Django app directories
           'OPTIONS': {
               'context_processors': [
                   'myapp.context_processors.global_context',
               ],
           },
       },
   ]

**APP_DIRS**: Whether to scan Django app directories (default: True)
**context_processors**: List of context processor paths for global template variables

Multiple Configurations
~~~~~~~~~~~~~~~~~~~~~~~

next.dj supports multiple NEXT_PAGES configurations, allowing you to have different routing strategies for different parts of your application:

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
- **Main site routes** in ``pages/`` directory
- **Admin routes** in ``admin_pages/`` directory
- **Different layout hierarchies** for different sections
- **Separate context processors** for different areas

Router Discovery
----------------

The system discovers routes by:

1. **Scanning app directories**: Each Django app's ``pages/`` directory
2. **Scanning root pages**: Project root ``pages/`` directory  
3. **Processing page files**: Converting ``page.py`` files to URL patterns
4. **Processing virtual routes**: Converting ``template.djx`` files to URL patterns

Route Caching
-------------

Routes are cached for performance. The cache is cleared when:

1. Django development server restarts
2. Page files are modified
3. Template files are modified
4. Settings are changed

Validation Checks
-----------------

The system includes comprehensive validation checks to prevent configuration errors:

**Configuration Validation** (``check_next_pages_configuration``):
- Validates NEXT_PAGES configuration structure
- Checks for required fields (BACKEND)
- Validates backend types and option structures
- Ensures all configured backends can be instantiated

**Page Structure Validation** (``check_pages_structure``):
- Validates directory names for proper parameter syntax
- Checks for missing page.py files in parameter directories
- Validates file organization and naming conventions

**Page Function Validation** (``check_page_functions``):
- Ensures page.py files have valid render functions or templates
- Validates function signatures and return types
- Prevents runtime errors during page rendering

**Template Validation** (``check_missing_templates``):
- Ensures every page.py has either a template attribute or template.djx file
- Prevents pages from being created without proper template definitions

**URL Pattern Validation** (``check_url_patterns``):
- Generates URL patterns and validates them for conflicts
- Checks for duplicate URL names and parameter consistency
- Identifies potential routing conflicts

**Context Function Validation** (``check_context_functions``):
- Validates that context functions return proper data types
- Ensures @context decorated functions return dictionaries when used without keys

Run validation checks:

.. code-block:: bash

   python manage.py check

Examples
--------

See the ``examples/`` directory in the source code for complete working examples:

- **file-routing/**: Basic file-based routing examples
- **pages/**: Page creation and template examples  
- **layouts/**: Layout inheritance examples
- **components/**: Component-based examples

Best Practices
--------------

1. **Use meaningful directory names**: They become URL segments
2. **Keep routes shallow**: Avoid deep nesting when possible
3. **Use parameters wisely**: Choose appropriate parameter types
4. **Test all routes**: Ensure they work correctly
5. **Follow naming conventions**: Use consistent directory and file names
6. **Handle errors gracefully**: Always provide fallback data in context functions
