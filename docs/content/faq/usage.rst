.. _faq-usage:

Usage Questions
===============

This page answers questions that come up while building a project with next.dj.

.. contents::
   :local:
   :depth: 2

How do I add a page
-------------------

Create a directory under the page root and add a ``page.py`` plus a ``template.djx`` inside it.
See :doc:`/content/howto/add-a-page` for a recipe.

How do I pass data to the template
----------------------------------

Use ``@context("key")`` inside ``page.py`` to publish a value.
The template renders the value as ``{{ key }}``.
See :doc:`/content/topics/context`.

How do I share data across pages
--------------------------------

Declare the context in a ``page.py`` that sits in a directory above the consuming page, and pass ``inherit_context=True`` to the decorator.
See :doc:`/content/howto/share-context-across-pages`.

How do I capture URL parameters
-------------------------------

Name the directory ``[param]`` for a string or ``[type:param]`` for a typed value.
Inside the page module annotate the parameter with ``DUrl[T]``.
See :doc:`/content/topics/file-router`.

How do I render a form
----------------------

Register a handler with ``@action`` and render the form with ``{% form @action="name" %}``.
See :doc:`/content/intro/tutorial04`.

How do I customise the static output
------------------------------------

Subclass ``StaticFilesBackend`` to adjust URLs or tag attributes and register the dotted path in ``DEFAULT_STATIC_BACKENDS``.
Subclass the abstract ``StaticBackend`` only for fully custom URL resolution.
See :doc:`/content/howto/write-a-static-backend`.

How do I test a page
--------------------

Use ``NextClient`` from ``next.testing.client``.
See :doc:`/content/topics/testing`.

How do I run the development server
-----------------------------------

Run ``uv run python manage.py runserver``.
The autoreloader picks up new page directories within a second.

How do I deploy in production
-----------------------------

See :doc:`/content/deployment/index`.

How do I integrate Django admin
-------------------------------

Mount ``admin.site.urls`` above ``include("next.urls")`` in ``config/urls.py``.
See :doc:`/content/howto/integrate-django-admin`.

How do I split routes across applications
-----------------------------------------

Two strategies.
Use ``APP_DIRS=True`` so every application contributes its own page tree.
Use ``DIRS`` to add project level page roots.
See :doc:`/content/topics/file-router`.

How do I share components across projects
-----------------------------------------

Place the shared components in one folder and reference it through ``DEFAULT_COMPONENT_BACKENDS["DIRS"]``.
See :doc:`/content/howto/share-components-across-projects`.

How do I translate URLs or templates
-------------------------------------

Internationalisation stays on Django's stack.
Configure ``LocaleMiddleware``, translation files, and ``i18n_patterns`` (or your preferred URL prefix strategy) the same way as in a stock Django project.
File routes resolve under whatever locale-aware prefix Django exposes. next.dj does not ship a separate translation mechanism for ``page.py`` files beyond ordinary Django template translation tags.

See Django's :doc:`translation overview <django:topics/i18n/index>`.

See Also
--------

.. seealso::

   :doc:`/content/howto/index` for recipes.
   :doc:`/content/topics/index` for in depth guides.
