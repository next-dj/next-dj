.. _intro-overview:

Overview
========

next.dj is a framework built on Django that turns the filesystem into your URL router, layout tree, and component registry.
It extends a regular Django project while leaving the ORM, admin, auth, and migrations to Django.

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
   Components carry their own Python file, template, and co-located CSS and JS, meaning those assets live in the same folder as the component.
   The framework discovers them by name and renders them through the ``{% component %}`` tag.

Form actions.
   A ``@action`` decorator registers a callable as a form handler under a required action name, for example ``@action("create_note", form_class=NoteForm)``.
   The ``{% form %}`` template tag points at that handler by the same name.
   The framework injects only the parameters that the handler signature asks for.

.. _intro-overview-django-unchanged:

What next.dj Does Not Replace
-----------------------------

The ORM, migrations, admin, auth, and middleware stay the same as in a stock Django project.
next.dj adds the ``NEXT_FRAMEWORK`` dict, includes ``next.urls`` for the file router, and resolves ``.djx`` through ``DjxTemplateLoader``.
Standard ``.html`` templates in other apps are unchanged.

For the design principles behind that split, read :doc:`/content/misc/design-philosophy`.

Key Terms
---------

You will see the nouns *page*, *layout*, *component*, *action*, and *context function* on every page.
:doc:`/content/misc/glossary` defines each one.

A Minimal Project
-----------------

Once installed, the smallest next.dj project is a ``page.py`` plus a ``template.djx`` under an app's ``pages/`` directory such as ``notes/pages/``.
It also needs the ``NEXT_FRAMEWORK`` block in ``config/settings.py`` and a one-line ``include("next.urls")`` in ``config/urls.py``.
:doc:`install` shows the full three-file shape with each block spelled out.
Every new directory under ``pages/`` then adds another page without touching the URL configuration.

When to Read the Tutorial
-------------------------

If you have used Django before and want to feel the framework, jump to :doc:`tutorial01`.
The five tutorial parts build a small Notes application that exercises every core subsystem.

.. seealso::

   :doc:`install` for environment setup.
   :doc:`whatsnext` for topic hubs after the tutorial.
   :doc:`/content/topics/index` for in-depth topic guides.
   :doc:`/content/ref/index` for the API reference.
