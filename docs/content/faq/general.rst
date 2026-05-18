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

Three tiers describe the public API.

Stable.
   Decorators, form base classes, template tags, marker types, and the documented settings keys.
   Import these from the top-level ``next.*`` packages.

Advanced.
   Backend base classes, factory helpers, and frozen spec types.
   Their signatures are intentionally stable.
   Use them when writing a custom backend or a custom renderer.

Internal hooks.
   Symbols whose names start with a single underscore, plus private submodule internals.
   Some packages re-export these names for testing and backend authoring convenience, but that does not make them part of the public API.
   Application code must not import them.
   A backend or extension author may use them when extending the dispatch or discovery pipelines, accepting that the names can change without notice.

See :doc:`/content/ref/forms` for a concrete example of how the tier model applies to ``next.forms``.

See Also
--------

.. seealso::

   :doc:`usage` for build-time questions.
   :doc:`troubleshooting` for runtime questions.
