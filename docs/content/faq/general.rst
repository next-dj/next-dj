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

Does the file router add per-request overhead
---------------------------------------------

Very little, and it does not grow with the number of pages.
The filesystem walk happens once at startup, and a request then resolves through a dictionary lookup for a static route or a walk down a trie of path segments for a parameterised one, so the cost scales with the depth of the request path.
See *Resolution performance* in :doc:`/content/topics/file-router` for the algorithm and the setting that restores the plain Django linear scan.

Which Django and Python versions are supported
----------------------------------------------

Python 3.12, 3.13, and 3.14, with Django 5.2, 6.0, and 6.1.
Python 3.14 needs Django 6.0 or newer, because Django 5.2 supports 3.12 and 3.13 only.
See the *Requirements* list in :doc:`/content/intro/install` for the combinations continuous integration tests.

Is next.dj production ready
---------------------------

Yes for a project that pins an exact release and reads what changed before it upgrades, and no for one that expects a frozen API and a long-term support line.
The code itself is held to strict gates, 100 percent line and branch coverage on ``next/`` and on every example, strict mypy, and a benchmark comparison that blocks a merge on a regression, as :doc:`/content/contributing/quality-gates` records.
What is not settled is the public API, so the documented surface is a contract that can still change in ways that require edits to application code, and :ref:`faq-safe-symbols` below defines exactly what it covers.
The manual carries no release history by policy, so each change states its own impact in the pull request that makes it.
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
The six extension mechanisms in :doc:`/content/topics/extending` cover the common cases.
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
The ``next.testing`` submodules are documented that way, so :doc:`/content/intro/tutorial05` imports ``SignalRecorder`` from ``next.testing.capture`` and :doc:`/content/topics/testing` imports ``NextClient`` and ``eager_load_pages`` from ``next.testing.client`` and ``next.testing.loaders`` directly.

Second, symbols whose names start with a single underscore are internal and may change without notice, even when they appear in a module ``__all__``.
The underscore rule is binding and overrides any incidental re-export.

A few underscore-free names carry a narrower cross-area contract, documented on the reference page of their subsystem (see :doc:`/content/ref/pages`).
The underscore rule holds for them, so they are not removed without notice, but they do not promise the application-facing stability of the Stable tier, which :doc:`/content/ref/index` names alongside the other tiers a subsystem page uses.

See :doc:`/content/ref/forms` for a concrete example of how the API tiers apply to ``next.forms``.

See also
--------

.. seealso::

   :doc:`usage` for build-time questions.
   :doc:`troubleshooting` for runtime questions.
   :doc:`/content/security/overview` for what the framework inherits from Django and what it adds.
