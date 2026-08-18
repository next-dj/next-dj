.. _faq-general:

General questions
=================

This page answers high-level questions about the project, its scope, and its lifecycle.

.. contents::
   :local:
   :depth: 2

What is next.dj and is it a Django replacement
----------------------------------------------

next.dj is a framework built on Django, not a replacement for it.
It adds file-based routing, a layout system, reusable components, form dispatch, and partial rendering on top of a regular Django project.
See :doc:`/content/intro/overview`, especially :ref:`intro-overview-django-unchanged`, for what stays stock Django versus what the framework adds.

Which Django and Python versions are supported
----------------------------------------------

The :doc:`/content/intro/install` *Requirements* list names the tested Python and Django combinations.

Is next.dj production ready
---------------------------

The answer has two halves, and the second one is the limiting factor.
The engineering gates are strict and verifiable, and the public API is not frozen.

Every merge passes the same gates.
The test suite enforces 100 percent line coverage of ``next/``, with only the per-area ``checks.py`` modules excluded.
A benchmark workflow runs on every pull request and fails at a ``median:99%`` regression gate, so a hot-path slowdown blocks the merge.
Ruff runs with ``select = ["ALL"]``, mypy runs strict over ``next/``, the documentation builds with warnings as errors, and the client runtime bundle is held to a gzip size budget.

The support matrix is tested rather than declared.
Continuous integration runs the suite against Python 3.12, 3.13, and 3.14 crossed with Django 5.2 and 6.0, excluding Python 3.14 on Django 5.2, and it runs against the built wheel rather than the source tree.
A separate compatibility suite runs against django-allauth, django-crispy-forms, django-htmx, and django-widget-tweaks, and every project under ``examples/`` ships tests held to 100 percent coverage of its own.
A deployment verifies its own configuration with ``manage.py check``, which runs the framework system checks contributed by eight subsystems (see :doc:`/content/ref/system-checks`).

The boundary is API stability.
The documented public surface is the contract, and :ref:`faq-safe-symbols` below defines exactly what it covers.
That contract can still change in ways that require edits to application code, and the manual carries no release history by policy, so each change states its own impact in the pull request that makes it.
Pin the Python and Django releases the matrix tests (see :doc:`/content/intro/install`), pin an exact next.dj release, and read the pull requests behind an upgrade before taking it.

How do I follow the project
---------------------------

Watch the repository on GitHub.
Releases ship through PyPI under the distribution name ``next.dj``, imported as ``next`` (see :doc:`/content/intro/install`).
Discussions and feature requests live on GitHub Discussions.

How is this different from plain Django forms
---------------------------------------------

A next.dj form needs no URL entry and no view.
Subclassing ``next.forms.Form`` or ``next.forms.ModelForm`` registers it and attaches a POST endpoint, CSRF, and a re-render-on-failure pipeline (see :doc:`/content/topics/forms/overview`).
A failed submission re-renders the origin page with the entered values and field errors instead of an error page, so you write no re-render code (see :doc:`/content/topics/forms/validation-rerender`).
A ``next.forms.FormWizard`` persists per-step data through a configured backend rather than hand-managed session keys (see :doc:`/content/topics/forms/wizard`).

When to use FormWizard versus rolling your own session logic
------------------------------------------------------------

Use ``next.forms.FormWizard`` when a flow spans several steps that share a final commit, where you would otherwise stash partial data in the session and wire step routing, back-navigation, and conditional branching by hand.
A single form, or two independent forms with no shared finalisation, does not need a wizard.

What about plugins
------------------

The project does not ship a plugin registry.
The five extension mechanisms in :doc:`/content/topics/extending` cover the common cases.
Distribute your customisations as ordinary Python packages.

What about a CLI
----------------

The framework does not add a new CLI.
Django's ``manage.py`` plus the framework system checks cover the operational surface.

.. _faq-safe-symbols:

Which symbols are safe to depend on
-----------------------------------

Two rules define the public surface.
First, anything exported from a top-level ``next.*`` package is safe to import, and so is any module the :doc:`/content/ref/index` pages document with an ``automodule`` entry.
The ``next.testing`` submodules are documented that way, so :doc:`/content/intro/tutorial05` and :doc:`/content/topics/testing` import ``NextClient``, ``SignalRecorder``, and ``eager_load_pages`` from ``next.testing.client``, ``next.testing.signals``, and ``next.testing.loaders`` directly.

Second, symbols whose names start with a single underscore are internal and may change without notice, even when they appear in a module ``__all__``.
The underscore rule is binding and overrides any incidental re-export.
See :doc:`/content/ref/forms` for a concrete example of how the API tiers apply to ``next.forms``.

See also
--------

.. seealso::

   :doc:`usage` for build-time questions.
   :doc:`troubleshooting` for runtime questions.
