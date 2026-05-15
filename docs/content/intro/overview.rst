.. _intro-overview:

Overview
========

next.dj is a Django library that turns the filesystem into your URL router, your layout tree, and your component registry.
You stay inside Django for models, admin, ORM, auth, and migrations.
You leave Django when laying out URLs, sharing context, composing components, and dispatching forms.

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
   A ``@context`` decorator publishes named values into the template scope and can opt into ``inherit_context=True`` to share values across descendants.

Components.
   A folder under the configured components root becomes a reusable template fragment.
   Components carry their own Python file, template, and co-located CSS and JS.
   The framework discovers them by name and renders them through the ``{% component %}`` tag.

Form actions.
   A ``@action`` decorator on a callable registers it as a form handler.
   The ``{% form %}`` template tag points at that handler by name.
   The framework injects only the parameters that the handler signature asks for.

What next.dj Does Not Replace
-----------------------------

next.dj does not replace Django, it depends on it.

- Django models, migrations, and the ORM stay the same.
- Django admin, auth, and middleware stack work without changes.
- Django settings still live in ``settings.py``, and next.dj reads its own knobs from a single ``NEXT_FRAMEWORK`` dict.
- Django templates and template tags continue to work, and the framework registers a few new tags as Django builtins at startup.

Glossary at a Glance
--------------------

Page.
   A directory with a ``page.py`` becomes a URL.
   Its sibling ``template.djx`` is the body that renders inside the layout chain.

Layout.
   A ``layout.djx`` in any ancestor directory wraps every descendant page.
   Layouts nest, with the closest layout being the innermost wrapper.

Component.
   A directory under the components root.
   Holds ``component.djx`` (template), optional ``component.py`` (context), and optional co-located ``component.css`` and ``component.js``.

Action.
   A Python callable decorated with ``@action("name")``.
   The framework gives it a stable URL and dispatches form submissions to it.

Context function.
   A callable decorated with ``@context("key")`` inside a ``page.py`` or ``layout.py``.
   Its return value lands in the template under the key.

DI marker.
   A type annotation such as ``DUrl[str]``, ``DQuery[int]``, or ``Depends(...)`` that asks the resolver for a specific source of data.

A Single-File Tour
------------------

Once installed, the smallest next.dj project lives in three files.

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
   :doc:`/content/topics/index` for in-depth topic guides.
   :doc:`/content/ref/index` for the API reference.
