.. _misc-project-status:

Project status
==============

This page states what a project can rely on when it depends on next.dj.
It covers the public API surface, the ``NEXT_FRAMEWORK`` settings contract, the supported Python and Django releases, what has to settle before the project leaves its current maturity classifier, who maintains it, the licence, and where a change reaches the reader.

.. contents::
   :local:
   :depth: 1

Maturity
--------

The framework is under active development, and the distribution carries the Alpha development-status classifier to say so.
A deployment therefore pins an exact release and re-reads this page before it upgrades.
Run ``uv run python manage.py check`` after an upgrade, because the framework reports a configuration mistake with a code and a hint, as :doc:`/content/ref/system-checks` describes.

What that classifier does not describe is how the code is held, which is the part a reader evaluating the project can verify.
Every merge carries 100 percent line and branch coverage over ``next/``, the same threshold on every project under ``examples/``, and the same on the TypeScript client runtime.
Mypy runs strict over the whole package, Ruff runs with every rule selected, and a benchmark job fails a pull request whose median regresses past the gate it sets.
The client bundle has a hard budget of 14 KB gzipped, checked on every run, because it ships on every page.
:doc:`/content/contributing/quality-gates` states what each gate measures and which command reproduces it.

Road to 1.0
-----------

Three things have to settle before the classifier moves past Alpha.
The public API has to freeze, which means the curated top-level ``next.*`` exports and the reference pages' ``automodule`` surface stop changing shape between releases.
The ``NEXT_FRAMEWORK`` key set has to freeze with it, so an upgrade adds keys and renames none.
The extension contracts have to freeze last, the backend base classes of every family and the ports in :doc:`/content/ref/ports`, because an extension written against them outlives the release it was written on.

Three subsystems are still moving.
Partial rendering carries the largest surface and the most open questions, and its own :doc:`/content/topics/partial-rendering/limitations` page states what the current model does not cover.
The client runtime moves with it, since the two halves of the protocol change together.
The static pipeline is the third, where asset name resolution and the rules a custom backend inherits are the parts a project extending it reads from the reference page rather than from a recipe.
Routing, pages, layouts, context, and form dispatch are the settled half of the tree.

The criterion for leaving Alpha is therefore not a feature count.
It is a run of releases in which none of the three contracts above breaks, which is what lets a project take an upgrade on the strength of the release alone rather than on the pull requests behind it.

Who maintains next.dj
---------------------

The project is maintained through the repository by its contributors, and it carries no company backing and no funded support contract.
Issues, pull requests, and security advisories are read, and the time to a first response depends on maintainer availability rather than on a service level the project promises, which :repo:`CONTRIBUTING.md <blob/main/CONTRIBUTING.md>` states in the same terms.
A report that carries a reproducible case is answered soonest, because triage is the step availability constrains most.
A deployment that needs a guaranteed response time plans for reading the code itself, which the MIT licence permits without restriction.

What counts as public API
-------------------------

Two rules define the surface, and :ref:`faq-safe-symbols` states both.
Anything exported from a top-level ``next.*`` package is safe to import, and so is any module a page under :doc:`/content/ref/index` documents with an ``automodule`` entry.
A symbol whose name starts with a single underscore is internal and may change without notice, even when it appears in a module ``__all__``.
The underscore rule is binding and overrides any incidental re-export, so a recipe that reaches for a private name carries a risk the project accepts on its own.

How NEXT_FRAMEWORK keys change
------------------------------

:doc:`/content/ref/settings` lists every key, its default, and its accepted shape, and that page is the contract.
The framework merges those defaults under the project dict, so a key a project never sets keeps the documented default and an upgrade that adds a key leaves an existing configuration working.
``manage.py check`` reports a settings mistake under one of four codes.

.. list-table:: Settings check codes
   :header-rows: 1
   :widths: 30 70

   * - Code
     - Reported for
   * - ``next.E035``
     - A top-level key the framework no longer knows.
   * - ``next.E076``
     - A value whose type the settings merge would discard.
   * - ``next.E077``
     - A ``NEXT_FRAMEWORK`` that is no dict at all.
   * - ``next.W072``
     - A non-bool value for a bool flag.

A renamed or removed key therefore fails a check run instead of degrading a deployment silently.
See :doc:`/content/deployment/settings` for the values a production deployment sets explicitly.

Supported Python and Django
---------------------------

The *Requirements* list in :doc:`/content/intro/install` names the supported Python and Django releases in one place.
Continuous integration installs the built wheel and runs the full test suite against every combination that list names.
:doc:`/content/contributing/quality-gates` describes how that matrix runs.

Where a change is announced
---------------------------

The repository ships no changelog file.
A change in behaviour lands with the pull request that makes it, and the pull request checklist requires the documentation to change in the same pull request.
A release is published to the Python Package Index from a version tag, so the distribution history there records what shipped.
A project that tracks changes closely follows the repository pull requests and re-reads the manual page for the subsystem it depends on.

The manual is built from the development branch, so it can describe work that no published release carries yet.
Every page names the version it was built from in the site header.
A reader who needs the manual for an installed distribution compares that stamp with the installed version and follows the repository pull requests for whatever the two do not share.

Security fixes
--------------

Security fixes are applied to the latest release line when practical, and the repository security policy asks a project to run the latest stable release.
A vulnerability is reported through the repository security advisories rather than through a public issue or pull request comment.
See :doc:`/content/security/reporting` for the process, the response expectations, and what falls outside the program.

Licence
-------

next.dj is released under the MIT licence, and :repo:`LICENSE <blob/main/LICENSE>` in the repository carries the full text.
The licence permits use, modification, and redistribution, including inside commercial work, and it ships the software without warranty.

See also
--------

.. seealso::

   :doc:`/content/topics/partial-rendering/limitations` for the boundaries the current model states about itself.
   :doc:`/content/faq/general` for the rules that define the safe import surface.
   :doc:`/content/contributing/quality-gates` for what every change passes before it merges.
