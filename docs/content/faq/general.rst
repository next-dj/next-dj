.. _faq-general:

General Questions
=================

This page answers high-level questions about the project, its scope, and its lifecycle.

.. contents::
   :local:
   :depth: 2

What Is next.dj and Is It a Django Replacement
----------------------------------------------

next.dj is a framework built on Django, not a replacement for it.
It adds file-based routing, a layout system, reusable components, and form dispatch on top of a regular Django project.
See :doc:`/content/intro/overview`, especially :ref:`intro-overview-django-unchanged`, for what stays stock Django versus what the framework adds.

Which Django and Python Versions Are Supported
----------------------------------------------

The :doc:`/content/intro/install` *Requirements* list names the tested Python and Django combinations.

Is next.dj Production Ready
---------------------------

next.dj is used in production.
Run ``manage.py check`` to confirm a deployment matches framework expectations, and pin a supported Python and Django release (see :doc:`/content/intro/install`).
See :ref:`faq-safe-symbols` below for guidance on the public API surface.

How Do I Follow the Project
---------------------------

Watch the repository on GitHub.
Releases ship through PyPI under the distribution name ``next.dj``, imported as ``next`` (see :doc:`/content/intro/install`).
Discussions and feature requests live on GitHub Discussions.

How Is This Different From Plain Django Forms
---------------------------------------------

A next.dj form needs no URL entry and no view.
Subclassing ``next.forms.Form`` or ``next.forms.ModelForm`` registers it and attaches a POST endpoint, CSRF, and a re-render-on-failure pipeline (see :doc:`/content/topics/forms/overview`).
A failed submission re-renders the origin page with the entered values and field errors instead of an error page, so you write no re-render code (see :doc:`/content/topics/forms/validation-rerender`).
A ``next.forms.FormWizard`` persists per-step data through a configured backend rather than hand-managed session keys (see :doc:`/content/topics/forms/wizard`).

When To Use FormWizard Versus Rolling Your Own Session Logic
------------------------------------------------------------

Use ``next.forms.FormWizard`` when a flow spans several steps that share a final commit, where you would otherwise stash partial data in the session and wire step routing, back-navigation, and conditional branching by hand.
A single form, or two independent forms with no shared finalisation, does not need a wizard.

What About Plugins
------------------

The project does not ship a plugin registry.
The five extension mechanisms in :doc:`/content/topics/extending` cover the common cases.
Distribute your customisations as ordinary Python packages.

What About a CLI
----------------

The framework does not add a new CLI.
Django's ``manage.py`` plus the framework system checks cover the operational surface.

.. _faq-safe-symbols:

Which Symbols Are Safe to Depend On
-----------------------------------

Two rules define the public surface.
First, anything exported from a top-level ``next.*`` package is safe to import.
Second, symbols whose names start with a single underscore are internal and may change without notice, even when they appear in a module ``__all__``.
The underscore rule is binding and overrides any incidental re-export.
See :doc:`/content/ref/forms` for a concrete example of how the API tiers apply to ``next.forms``.

See Also
--------

.. seealso::

   :doc:`usage` for build-time questions.
   :doc:`troubleshooting` for runtime questions.
