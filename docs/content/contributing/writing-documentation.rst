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
   * - ``misc/``
     - Glossary, design philosophy, and :doc:`/content/misc/examples`.
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
   Aim for under 150 lines.
   Multi-part integrations (streaming, admin shell, cross-cutting security) may exceed that when splitting the flow would hurt verification steps.

Reference.
   Module Summary, Public API, Configuration, Signals, See Also.
   Body is generated through ``autodoc`` directives.

Internals.
   Overview, Pipeline (with a mermaid diagram), Implementation Notes, Extension Points, See Also.

One Canonical Source Per Fact
------------------------------

Each technical fact has one canonical home in the documentation tree.
Examples are the resolution order for context, the exact order of startup hooks, and a specific setting name.
Other pages may reference it with ``.. seealso::`` or a sentence-level link, but they should not reproduce the fact independently.
When the implementation changes, updating one page is enough.

Checklist for settings and system checks.
   Before describing a ``NEXT_FRAMEWORK`` key or a system check error, search ``docs/content/`` for every existing mention of that name.
   Read the code path that implements it (start from ``next/conf/defaults.py`` for settings).
   Write or update the canonical description in ``ref/settings.rst`` or ``ref/system-checks.rst``, then make every other mention a sentence-level cross-reference or a ``.. seealso::`` pointer.
   This prevents the divergence where ``ref/`` and ``deployment/`` describe the same flag with different meanings.
   When you add or change a ``next.*.checks`` error or warning id, update the tables in ``ref/system-checks.rst`` in the same change.

Version Numbers and Compatibility Notes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Do not mention the framework's current version number in user-facing pages.
Do not include backward-compatibility notes, migration guidance, or phrases like "as of version X" or "this was changed in Y".
The documentation describes how the framework works now.
Historical context belongs in the changelog, not in the docs.
Do not use Sphinx ``.. versionadded::`` or ``.. versionchanged::`` directives in user-facing pages. They duplicate the same problem under a different syntax.

Runtime matrices
~~~~~~~~~~~~~~~~

Document supported Python and Django releases in one place: :doc:`/content/intro/install` under *Requirements*.
On other pages link back with a short sentence such as "Use a supported Python and Django release (see :doc:`/content/intro/install`)" instead of copying the bullet list.
Fragmented matrices drift out of sync with ``pyproject.toml`` and CI.

Examples and Code Blocks
~~~~~~~~~~~~~~~~~~~~~~~~

Keep filenames aligned with the Notes tutorial unless the page names another sample project.

Paste runnable snippets when practical.

Prefer adapting excerpts from ``examples/`` over speculative shortcuts.

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

Coverage Map
------------

When you change ``next/`` code, check whether the corresponding documentation needs updating.
The table below lists the public package, its primary narrative page, and its reference page.

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Package
     - Narrative (topics / howto)
     - Reference
   * - ``next.pages``
     - :doc:`/content/topics/pages`, :doc:`/content/topics/layouts`, :doc:`/content/topics/context`
     - :doc:`/content/ref/pages`
   * - ``next.components``
     - :doc:`/content/topics/components`
     - :doc:`/content/ref/components`
   * - ``next.urls``
     - :doc:`/content/topics/file-router`, :doc:`/content/topics/url-reversing`
     - :doc:`/content/ref/urls`
   * - ``next.forms``
     - :doc:`/content/topics/forms/index`
     - :doc:`/content/ref/forms`
   * - ``next.static``
     - :doc:`/content/topics/static-assets/index`
     - :doc:`/content/ref/static`
   * - ``next.deps``
     - :doc:`/content/topics/dependency-injection`
     - :doc:`/content/ref/deps`
   * - ``next.conf``
     - :doc:`/content/topics/project-layout`
     - :doc:`/content/ref/conf`, :doc:`/content/ref/settings`
   * - ``next.server``
     - :doc:`/content/internals/autoreload`
     - :doc:`/content/ref/server`
   * - ``next.testing``
     - :doc:`/content/topics/testing`
     - :doc:`/content/ref/testing`
   * - ``next.apps``
     - :doc:`/content/internals/overview`
     - :doc:`/content/ref/apps`
   * - ``next.signals``
     - :doc:`/content/topics/signals`
     - :doc:`/content/ref/signals`

When you edit the signal aggregator in ``next/signals.py``, update :doc:`/content/topics/signals` and :doc:`/content/ref/signals` so every re-exported name and payload matches the module.

When you add an example to ``examples/``, update ``examples/README.md`` and add or adjust the row in :doc:`/content/misc/examples`.

Cross references
~~~~~~~~~~~~~~~~

Prefer absolute paths from the manual root (for example ``:doc:`/content/topics/pages```) in new prose so links remain valid if a page moves between toctrees.

See Also
--------

.. seealso::

   :doc:`style-guide` for the style rules.
   :doc:`/content/internals/contributing-notes` for framework code conventions.
