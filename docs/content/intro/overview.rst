.. _intro-overview:

Overview
========

next.dj is a framework built on Django that turns the filesystem into your URL router, layout tree, and component registry.
It extends a regular Django project while leaving the ORM, admin, auth, and migrations to Django.

This page describes the mental model.
Read it once before the tutorial, then refer back when the layout of a real project surprises you.

What next.dj Adds
-----------------

next.dj layers five things on top of a regular Django project.

File router.
   Every directory under a configured page root becomes a URL, and a ``page.py`` turns it into a navigable page.
   A bracketed segment such as ``[slug]`` becomes a captured URL parameter.
   See :doc:`/content/topics/file-router`.

Layouts and context.
   A ``layout.djx`` wraps every page under its directory, and layouts nest down the tree.
   A ``@context`` decorator publishes named values into the template scope, optionally inherited by every descendant page.
   See :doc:`/content/topics/layouts` and :doc:`/content/topics/context`.

Components.
   A folder under the configured components root becomes a reusable template fragment with optional Python, CSS, and JS files.
   The framework discovers components by name and renders them through the ``{% component %}`` tag.
   See :doc:`/content/topics/components`.

Form actions.
   Subclassing ``next.forms.Form`` or ``next.forms.ModelForm`` registers the form under a ``snake_case`` name, rendered by ``{% form "name" %}`` and validated into its ``on_valid`` method.
   Plain functions with no form can also register as actions with ``@action("name")``.
   See :doc:`/content/topics/forms/overview`.

Partial rendering.
   A ``{% zone %}`` block names a slice of a page the server can re-render on its own, and a form, filter, or link targets that zone.
   Every interaction degrades to a full page cycle when JavaScript is off.
   See :doc:`/content/topics/partial-rendering/index`.

.. _intro-overview-django-unchanged:

What next.dj Does Not Replace
-----------------------------

The ORM, migrations, admin, auth, and middleware stay the same as in a stock Django project.
next.dj adds the ``NEXT_FRAMEWORK`` dict, includes ``next.urls`` for the file router, and resolves ``.djx`` through ``DjxTemplateLoader``.
Standard ``.html`` templates in other apps are unchanged.

For the design principles behind that split, read :doc:`/content/misc/design-philosophy`.

The nouns *page*, *layout*, *component*, *action*, and *context function* appear on every documentation page.
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
The six tutorial parts build a small Notes application that exercises every core subsystem.
The first five wire up routing, layouts, components, forms, and editing, and the sixth makes the Notes index update in place with partial rendering.

.. seealso::

   :doc:`install` for environment setup.
   :doc:`whatsnext` for topic hubs after the tutorial.
   :doc:`/content/topics/index` for in-depth topic guides.
   :doc:`/content/ref/index` for the API reference.
