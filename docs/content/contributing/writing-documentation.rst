.. _contributing-writing-documentation:

Writing Documentation
=====================

This page describes how to write new documentation pages for next.dj and how to review pull requests that touch the docs.

.. contents::
   :local:
   :depth: 2

Where Pages Live
----------------

The documentation tree lives under ``docs/content/``.
The layout follows the Django convention.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Section
     - Purpose
   * - ``intro/``
     - Tutorial pages and the install guide.
   * - ``topics/``
     - In depth coverage of each subsystem.
   * - ``howto/``
     - Recipes for common tasks.
   * - ``ref/``
     - Module by module API reference.
   * - ``internals/``
     - How the framework works under the hood.
   * - ``deployment/``
     - Production deployment guidance.
   * - ``security/``
     - Threat model and best practices.
   * - ``faq/``
     - Common questions in three buckets.
   * - ``releases/``
     - Per release notes.
   * - ``misc/``
     - Glossary and design philosophy.
   * - ``contributing/``
     - Contribution workflow and writing guidance.

Picking the Right Section
-------------------------

Use this decision tree when you are unsure where a new page belongs.

I am writing a step by step lesson with checkpoints.
   It belongs under ``intro/``.

I am explaining a concept in depth.
   It belongs under ``topics/``.

I am answering a task shaped question (How do I X).
   It belongs under ``howto/``.

I am documenting a module, decorator, or signal.
   It belongs under ``ref/``.

I am explaining how a subsystem works inside.
   It belongs under ``internals/``.

If still in doubt, ask in a draft pull request.

Page Templates
--------------

Each section uses a consistent template.

Tutorial.
   Goal, Prerequisites, Walkthrough, Checkpoint, Next Steps.

Topic.
   Overview, Concepts, Usage, Common Patterns, See Also.
   Starts with a leading paragraph and ``.. contents:: :local:``.

How-To.
   Problem, Solution, Walkthrough, Verification, See Also.
   Under 150 lines.

Reference.
   Module Summary, Public API, Configuration, Signals, See Also.
   Body is generated through ``autodoc`` directives.

Internals.
   Overview, Pipeline (with a mermaid diagram), Implementation Notes, Extension Points, See Also.

Style Rules
-----------

Read :doc:`style-guide` before writing.
The rules cover punctuation, sentence length, headings, code blocks, admonitions, and links.

Diagrams
--------

Every internals page includes a mermaid diagram.
Diagrams live inline through ``.. mermaid::`` directives.

Workflow
--------

A typical change goes through three steps.

1. Branch from ``main``.
2. Add or update pages under ``docs/content/``.
3. Build with ``uv run --group docs sphinx-build -nW --keep-going docs docs/_build``.

A green build is a hard precondition for merge.
Local builds reveal anchor and cross reference issues quickly.

System Checks
-------------

Run ``uv run python manage.py check`` after every change that affects the API surface or the system checks.
The output makes sure that no contributed check broke during development.

Linting the Docs
----------------

The project uses ``doc8`` for RST style.

.. code-block:: bash
   :caption: shell

   uv run doc8 docs/content

The linter catches trailing whitespace, lines that exceed 200 characters, and inconsistent indentation.

Translation Notes
-----------------

The documentation is written in English.
Translations are not part of the project at this time.

See Also
--------

.. seealso::

   :doc:`style-guide` for the style rules.
   :doc:`/content/internals/contributing-notes` for framework code conventions.
