.. _misc-project-status:

Project status
==============

This page states what a project can rely on when it depends on next.dj.
It covers the public API surface, the ``NEXT_FRAMEWORK`` settings contract, the supported Python and Django releases, the licence, and where a change reaches the reader.

.. contents::
   :local:
   :depth: 1

Maturity
--------

The framework is under active development, and the repository README asks a project to treat a release as evolving until it validates the behaviour that project depends on.
A deployment therefore pins an exact release and re-reads this page before it upgrades.
Run ``uv run python manage.py check`` after an upgrade, because the framework reports a configuration mistake with a code and a hint, as :doc:`/content/ref/system-checks` describes.

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
A key the framework no longer knows is reported as ``next.E035`` at ``manage.py check``, a value the settings merge would discard is ``next.E076``, a ``NEXT_FRAMEWORK`` that is no dict at all is ``next.E077``, and a non-bool value for a bool flag is ``next.W072``.
A renamed or removed key therefore fails a check run instead of degrading a deployment silently.
See :doc:`/content/deployment/settings` for the values a production deployment sets explicitly.

Supported Python and Django
---------------------------

The distribution requires Python 3.12 or newer and Django 5.2 or newer below 6.1.
Continuous integration installs the built wheel and runs the full test suite against every combination in the table below.

.. list-table:: Tested combinations
   :header-rows: 1
   :widths: 30 70

   * - Python
     - Django
   * - 3.12
     - 5.2 and 6.0
   * - 3.13
     - 5.2 and 6.0
   * - 3.14
     - 6.0

The matrix excludes Python 3.14 against Django 5.2, so Python 3.14 runs against Django 6.0 only.
:doc:`/content/contributing/quality-gates` describes how that matrix runs.

Where a change is announced
---------------------------

The repository ships no changelog file.
A change in behaviour lands with the pull request that makes it, and the pull request checklist requires the documentation to change in the same pull request.
A release is published to the Python Package Index from a version tag, so the distribution history there records what shipped.
A project that tracks changes closely follows the repository pull requests and re-reads the manual page for the subsystem it depends on.

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
