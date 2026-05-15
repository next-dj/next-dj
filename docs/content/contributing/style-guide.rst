.. _contributing-style-guide:

Documentation Style Guide
=========================

Every documentation page follows the rules on this page.
Reviewers cite this guide during pull request review.

.. contents::
   :local:
   :depth: 2

Language and Tone
-----------------

The documentation is written in English.
The voice is third person present tense.
Avoid marketing tone, exclamation marks, and the words ``obviously``, ``simply``, ``just``.

Punctuation
-----------

Prose uses periods, commas, and question marks only.

Forbidden in running prose.
   ``;``
   ``:`` outside of a list lead in (such as ``See also:``)
   Em dash ``—``
   En dash ``–``

Allowed everywhere.
   These characters belong in code, paths, RST role syntax, headings, frontmatter, and inside list items that name two parallel items.

ASCII Art and Pipes
-------------------

ASCII art is forbidden in running prose.
Tables use ``.. list-table::`` or ``.. csv-table::``.
The ``|`` character appears only in code blocks and in RST substitutions.

Diagrams
--------

Every diagram is a ``.. mermaid::`` block inside the page.
External Excalidraw, PlantUML, and SVG sources are not used.

Sentences
---------

One sentence per line (semantic newlines).
Each statement is a complete sentence.
No fragments.

Paragraphs
----------

Two to five sentences per paragraph.
When the scope changes, introduce a new heading rather than a sixth sentence.

Headings
--------

RST underlines use ``=``, ``-``, ``~``, ``^``.
One H1 per file.
Title Case for every heading.
A heading never ends with punctuation.

Code Blocks
-----------

Use ``.. code-block:: <language>`` with an explicit language.
Add a ``:caption:`` that names the file.
DJX and Django templates use ``jinja`` as the language for syntax highlighting.

Example.

.. code-block:: jinja
   :caption: notes/routes/template.djx

   <h1>{{ title }}</h1>

Forbidden inside code blocks meant to be runnable.
   ``...`` ellipses.
   Shell prompts (``$``, ``>>>``) except in transcript blocks.

Cross References
----------------

Use ``:doc:`` for whole page links.
Use ``:ref:`` for anchors.
Every topic page declares an anchor at the top in the form ``.. _topics-<area>:``.

Admonitions
-----------

Allowed types.

- ``.. note::`` for nonessential context.
- ``.. warning::`` for foot guns.
- ``.. seealso::`` for sibling links.
- ``.. versionadded::`` and ``.. versionchanged::`` for release aware notes.

Each admonition holds one paragraph.

Inline Code
-----------

Double back ticks for code, paths, configuration keys, and signal names.

Lists
-----

Each list item is a complete sentence ending in a period.
Items in one list share grammar (all noun phrases, all verb phrases, all complete sentences).

Page Templates
--------------

Read :doc:`writing-documentation` for the section specific templates.
Templates apply consistently inside each section.

Pull Request Checklist
----------------------

A documentation pull request lands when.

- ``uv run --group docs sphinx-build -nW --keep-going docs docs/_build`` is green.
- ``uv run doc8 docs/content`` is green.
- Every new section follows its template.
- Every internal page has a mermaid diagram.
- Cross references resolve.

See Also
--------

.. seealso::

   :doc:`writing-documentation` for the broader workflow.
   :doc:`/content/internals/contributing-notes` for code conventions.
