.. _faq-general:

General Questions
=================

This page answers high level questions about the project, its scope, and its lifecycle.

.. contents::
   :local:
   :depth: 2

What is next.dj
---------------

next.dj is a Django library that turns the filesystem into your URL router, your layout tree, and your component registry.
It depends on Django and works inside any Django project.
See :doc:`/content/intro/overview` for the mental model.

Is next.dj a Django replacement
-------------------------------

No.
next.dj depends on Django for models, the ORM, admin, auth, and migrations.
It replaces only the URL configuration boilerplate, the view per URL pattern, and the verbose template inheritance.

Which Django versions are supported
-----------------------------------

The 0.5 line supports Django 4.2, 5.0, 5.1, 5.2, and 6.0.

Which Python versions are supported
-----------------------------------

The 0.5 line supports Python 3.12, 3.13, and 3.14.

Is next.dj production ready
---------------------------

The 0.5 line is the current active series.
The project is still pre 1.0 so breaking changes can happen between minor versions.

Where does the name come from
-----------------------------

The name borrows from Next.js for the file routing inspiration and adds the ``.dj`` suffix to signal the Django ground floor.

How do I follow the project
---------------------------

Watch the repository on GitHub.
Releases ship through PyPI under ``next-dj``.
Discussions and feature requests live on GitHub Discussions.

What about plugins
------------------

The project does not ship a plugin registry.
The five extension mechanisms in :doc:`/content/topics/extending` cover the common cases.
Distribute your customisations as ordinary Python packages.

What about a CLI
----------------

The framework does not add a new CLI.
Django's ``manage.py`` plus the framework system checks cover the operational surface.

See Also
--------

.. seealso::

   :doc:`usage` for build-time questions.
   :doc:`troubleshooting` for runtime questions.
