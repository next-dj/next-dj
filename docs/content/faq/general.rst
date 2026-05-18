.. _faq-general:

General Questions
=================

This page answers high level questions about the project, its scope, and its lifecycle.

.. contents::
   :local:
   :depth: 2

What is next.dj and is it a Django replacement
-----------------------------------------------

next.dj is a framework built on Django, not a replacement for it.
It adds file-based routing, a layout system, reusable components, and form dispatch on top of a regular Django project.
See :doc:`/content/intro/overview`, especially :ref:`intro-overview-django-unchanged`, for what stays stock Django versus what the framework adds.

Which Django and Python versions are supported
----------------------------------------------

The :doc:`/content/intro/install` *Requirements* list names the tested Python and Django combinations.

Is next.dj production ready
---------------------------

next.dj is used in production.
See `Which symbols are safe to depend on`_ below for guidance on the public API surface.

How do I follow the project
---------------------------

Watch the repository on GitHub.
Releases ship through PyPI under the project name ``next.dj``.
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

Which symbols are safe to depend on
------------------------------------

Anything exported from a top-level ``next.*`` package is safe to import, while symbols whose names start with a single underscore are internal and may change without notice.
See :doc:`/content/ref/forms` for a concrete example of how the API tiers apply to ``next.forms``.

See Also
--------

.. seealso::

   :doc:`usage` for build-time questions.
   :doc:`troubleshooting` for runtime questions.
