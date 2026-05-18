.. _intro-overview:

Overview
========

next.dj is a framework built on Django that turns the filesystem into your URL router, layout tree, and component registry.
It adds file-based routing, layouts and context, components, and form dispatch while leaving the ORM, admin, auth, and migrations to Django.

This page describes the mental model.
Read it once before the tutorial, then refer back when the layout of a real project surprises you.

What next.dj Adds
-----------------

next.dj layers four things on top of a regular Django project.

File router.
   Every directory under a configured page root becomes a URL.
   A ``page.py`` in that directory turns it into a navigable page.
   A bracketed segment such as ``[slug]`` becomes a captured URL parameter.

Layouts and context.
   A ``layout.djx`` wraps every page under its directory.
   Layouts nest down the tree.
   A ``@context`` decorator publishes named values into the template scope.
   A context value can also be inherited, so every page below the directory that declares it can read it.

Components.
   A folder under the configured components root becomes a reusable template fragment.
   Components carry their own Python file, template, and co-located CSS and JS.
   The framework discovers them by name and renders them through the ``{% component %}`` tag.

Form actions.
   A ``@action`` decorator registers a callable as a form handler under a required action name, for example ``@action("create_note", form_class=NoteForm)``.
   The ``{% form %}`` template tag points at that handler by the same name.
   The framework injects only the parameters that the handler signature asks for.

.. _intro-overview-django-unchanged:

What next.dj Does Not Replace
-----------------------------

The ORM, migrations, admin, auth, and middleware stay the same as in a stock Django project.
next.dj adds the ``NEXT_FRAMEWORK`` dict, includes ``next.urls`` for the file router, and resolves ``.djx`` through ``DjxTemplateLoader``. Standard ``.html`` templates in other apps are unchanged.

For the design principles behind that split, read :doc:`/content/misc/design-philosophy`.

Key Terms
---------

You will see these nouns on every page.

Page
   A directory with a ``page.py`` module that the file router maps to a URL.

Layout
   A ``layout.djx`` template that wraps every page below it in the tree.

Component
   A reusable template fragment with its own context and co-located assets.

Action
   A handler registered with ``@action`` that receives a form submission.

Context function
   A callable decorated with ``@context`` that publishes a value into the template scope.

DI marker
   A typed annotation such as ``DUrl`` that the resolver fills from the request.

Expand these definitions, spelling rules for route names, and the full term list in :doc:`/content/misc/glossary`.
For design rationale, see :doc:`/content/misc/design-philosophy`.

A Minimal Project
-----------------

Once installed, the smallest next.dj project is the three files below plus a one-line edit to ``config/urls.py``.

.. code-block:: python
   :caption: notes/routes/page.py

   from next.pages import context


   @context("title")
   def page_title() -> str:
       return "Notes"

.. code-block:: jinja
   :caption: notes/routes/template.djx

   <h1>{{ title }}</h1>

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "PAGES_DIR": "routes",
               "OPTIONS": {"context_processors": []},
           }
       ],
   }

Add ``include("next.urls")`` to ``config/urls.py`` and the URL ``/`` renders ``<h1>Notes</h1>``.
Every new directory under ``routes/`` adds another page without touching the URL configuration.

When to Read the Tutorial
-------------------------

If you have used Django before and want to feel the framework, jump to :doc:`tutorial01`.
The five tutorial parts build a small Notes application that exercises every core subsystem.

.. seealso::

   :doc:`install` for environment setup.
   :doc:`whatsnext` for topic hubs after the tutorial.
   :doc:`/content/topics/index` for in-depth topic guides.
   :doc:`/content/ref/index` for the API reference.
