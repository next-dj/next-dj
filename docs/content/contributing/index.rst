.. _contributing:

Contributing
============

This section covers contributions to next.dj, code and documentation alike.
It states the gates a change passes before it merges and points at the conventions each kind of change follows.

Code contributions start with :repo:`CONTRIBUTING.md <blob/main/CONTRIBUTING.md>` in the repository root, which covers environment setup, the project layout, the test commands, and the pull request process.
:doc:`quality-gates` states what continuous integration measures on every pull request, so a contributor knows what has to pass before a review starts.
For **internal framework conventions** (module layout, naming, signal and system-check rules) read :doc:`/content/internals/contributing-notes`.

Documentation contributions stay inside this section.
:doc:`writing-documentation` explains how to author, structure, and build a page, and :doc:`style-guide` states the prose rules a reviewer cites during review.

:doc:`quality-gates`
   The automated gates every pull request meets, from the coverage threshold to the benchmark comparison.

:doc:`writing-documentation`
   How to author, structure, and build the documentation.

:doc:`style-guide`
   Prose and formatting conventions the documentation follows.

.. toctree::
   :hidden:
   :maxdepth: 1

   quality-gates
   writing-documentation
   style-guide
