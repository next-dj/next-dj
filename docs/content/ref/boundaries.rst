.. _ref-boundaries:

Boundaries reference
====================

Module summary
--------------

``next.boundaries`` builds the graph of edges between the applications of a project, judges it against the boundary rules and the ``boundaries.toml`` ratchet, and reports the result as system checks under the ``boundaries`` tag.
The package exports ``App``, ``BaselineError``, ``BoundariesManager``, ``BoundariesUnavailableError``, ``Debt``, ``Edge``, ``EdgeKind``, ``Finding``, ``Graph``, ``ImportScanError``, ``RULES``, ``Refrozen``, ``Report``, ``Rule``, ``Scan``, ``Tangle``, ``UnknownAppError``, and ``boundaries_manager``, plus the ``signals`` module.
Every name but the exceptions and ``signals`` loads on first use, so importing ``next.signals`` loads neither the scan engine nor grimp.
``next.boundaries.checks`` registers ``check_boundaries``, which ``next.checks`` re-exports, and stays out of the ``next.boundaries`` namespace.
grimp is an optional dependency that the ``boundaries`` extra installs, and the package imports without it.
:doc:`/content/topics/boundaries` covers the rules and the workflow.

Check codes
-----------

Every boundary code but ``next.E172`` is built by ``check_boundaries`` from one row of ``RULES``, which holds its level, its hint, and the section of :doc:`/content/topics/boundaries` its hint points to.
``next.E172`` belongs to the ``NEXT_FRAMEWORK`` checks of ``next.conf.checks`` and carries the ``next`` tag, while the rest carry ``boundaries`` alone.
The ``Recorded`` column says whether ``boundaries.toml`` may record the code.
:doc:`system-checks` lists the condition of each code beside every other framework check.

.. list-table::
   :header-rows: 1
   :widths: 13 10 10 67

   * - Code
     - Level
     - Recorded
     - Section
   * - ``next.E167``
     - Error
     - Yes
     - :ref:`topics-boundaries-no-cycles`
   * - ``next.E168``
     - Error
     - No
     - :ref:`topics-boundaries-ratchet`
   * - ``next.E169``
     - Error
     - No
     - :ref:`topics-boundaries-ratchet`
   * - ``next.E170``
     - Error
     - No
     - :ref:`topics-boundaries-ratchet`
   * - ``next.E171``
     - Error
     - No
     - :ref:`topics-boundaries-strict-mode`
   * - ``next.E172``
     - Error
     - No
     - :ref:`topics-boundaries-strict-mode`
   * - ``next.E173``
     - Error
     - No
     - :ref:`topics-boundaries-import-graph`
   * - ``next.W136``
     - Warning
     - Yes
     - :ref:`topics-boundaries-public-modules`
   * - ``next.W137``
     - Warning
     - Yes
     - :ref:`topics-boundaries-page-trees`
   * - ``next.W138``
     - Warning
     - Yes
     - :ref:`topics-boundaries-visible-to`
   * - ``next.W139``
     - Warning
     - Yes
     - :ref:`topics-boundaries-writes`
   * - ``next.W140``
     - Warning
     - Yes
     - :ref:`topics-boundaries-get-model`
   * - ``next.W141``
     - Warning
     - Yes
     - :ref:`topics-boundaries-di-keys`
   * - ``next.W142``
     - Warning
     - Yes
     - :ref:`topics-boundaries-receivers`
   * - ``next.W143``
     - Warning
     - Yes
     - :ref:`topics-boundaries-sending-signals`
   * - ``next.W144``
     - Warning
     - Yes
     - :ref:`topics-boundaries-zones`
   * - ``next.W145``
     - Warning
     - Yes
     - :ref:`topics-boundaries-models`
   * - ``next.W146``
     - Warning
     - Yes
     - :ref:`topics-boundaries-aggregators`
   * - ``next.W147``
     - Warning
     - Yes
     - :ref:`topics-boundaries-file-names`
   * - ``next.W148``
     - Warning
     - No
     - :ref:`topics-boundaries-installing`
   * - ``next.W149``
     - Warning
     - Yes
     - :ref:`topics-boundaries-foreign-permissions`
   * - ``next.I001``
     - Info
     - No
     - :ref:`topics-boundaries-installing`

The message of a finding names the file relative to ``BASE_DIR`` and its line as the object of the check, and the hint closes with the full address of the section, such as ``https://next-dj.readthedocs.io/en/latest/content/topics/boundaries.html#no-cycles``.

The manager
-----------

``boundaries_manager`` is the one ``BoundariesManager`` the check and ``manage.py next boundaries`` share.
``scan()`` builds the graph, the DI keys, and the findings on first use and keeps them for the rest of the check run, keyed on the router manager of the run like the run memos of ``next.checks.common``, and ``reset()`` drops the held scan.
``graph()`` and ``findings()`` read that scan, and ``report()`` judges its findings against ``boundaries.toml`` and the mode.
``refreeze()`` writes ``boundaries.toml`` and sends ``baseline_refrozen``.
The import scan of a project with applications raises ``BoundariesUnavailableError`` while grimp is missing, which the ``available`` property and ``disabled()`` answer ahead of time, and ``refreeze()`` without ``BASE_DIR`` raises ``BaselineError`` first.
``mode`` answers the mode ``NEXT_FRAMEWORK["BOUNDARIES"]`` names, ``"warn"`` for any value but ``"strict"``, and ``strict`` whether that mode is ``"strict"``.
``disabled()`` answers why the boundaries go unchecked, ``None`` while they are checked, a ``next.I001`` finding without grimp in the ``"warn"`` mode, and a ``next.W148`` finding in the ``"strict"`` mode without grimp or without ``BASE_DIR``, and ``check_boundaries`` reports exactly that finding in place of the scan.

.. autoclass:: next.boundaries.BoundariesManager
   :members:

.. autoclass:: next.boundaries.Report
   :members:

.. autoclass:: next.boundaries.Refrozen
   :members:

The graph
---------

``App`` is a node named by its label, with the dotted ``name`` and the ``path`` of its package.
``Edge`` is one reason the application ``source`` depends on the application ``target``, with its ``kind``, the ``file`` and ``line`` it was found at, and a ``detail`` such as the imported module or the related field.
``kind`` is one of ``import``, ``fk``, ``get_model``, ``signal``, ``di``, ``zone``, ``component``, and ``perm``.
``Graph`` holds the applications and the edges, answers the edges that leave and reach one application, and groups the applications that reach one another through ``tangles()``, each with its labels, its witness cycle, and the first edge of every ordered pair, which is where each ``next.E167`` points.

.. autoclass:: next.boundaries.App
   :members:

.. autoclass:: next.boundaries.Edge
   :members:

.. autoclass:: next.boundaries.Graph
   :members:

Rules and findings
------------------

``RULES`` maps every boundary code built from the graph to its ``Rule``, the code, the level, the hint, and the anchor of its section.
``Rule.doc`` is the full address of the section, and ``Rule.full_hint`` the hint followed by that address.
``Finding`` is one broken rule, with the file relative to ``BASE_DIR``, the line, the code, the label of the application, and the message.

.. autoclass:: next.boundaries.Rule
   :members:

.. autoclass:: next.boundaries.Finding
   :members:

Exceptions
----------

``BoundariesUnavailableError`` says grimp is missing, and ``ImportScanError`` says grimp could not build the import graph, keeping the ``reason``.
``BaselineError`` refuses a ``boundaries.toml`` that cannot be read or written, keeping the ``path`` and the ``reason``.
``UnknownAppError`` names a label that is no application of the project, keeping the ``label``.
``manage.py next boundaries`` turns each of them into a ``CommandError``.

.. autoexception:: next.boundaries.BoundariesUnavailableError

.. autoexception:: next.boundaries.ImportScanError

.. autoexception:: next.boundaries.BaselineError

.. autoexception:: next.boundaries.UnknownAppError

Signals
-------

``baseline_refrozen`` is sent once ``refreeze()`` has written the file, with the ``BoundariesManager`` class as the sender and the ``Refrozen`` value as the ``refrozen`` keyword argument.
It is built with ``use_caching=True`` and is importable from ``next.signals`` as well.

.. code-block:: python
   :caption: notes/signals.py, a receiver on the refreeze

   from next import receiver
   from next.boundaries.signals import baseline_refrozen

   @receiver(baseline_refrozen)
   def log_refreeze(sender, refrozen, **kwargs) -> None:
       print(f"{refrozen.path} records {refrozen.recorded} findings.")

The boundaries.toml format
--------------------------

``boundaries.toml`` lives at ``BASE_DIR``, and ``manage.py next boundaries --refreeze`` is the only writer.
The file declares ``version = 1`` and holds one ``[[violation]]`` table per file and code, sorted by file and then by code.
Each table holds exactly three keys, ``file`` with the path relative to ``BASE_DIR`` in forward slashes, ``code`` with a recordable code from the table above, and ``count`` with a whole number of one or more.

.. code-block:: toml
   :caption: boundaries.toml

   version = 1

   [[violation]]
   file = "library/admin.py"
   code = "next.W136"
   count = 1

``next.E170`` refuses a file that is no valid TOML, a top-level key other than ``version`` and ``violation``, a version other than ``1``, a table with a missing or extra key, an empty file name, a code that cannot be recorded, a count below one, and a file and code recorded twice.

See also
--------

.. seealso::

   :doc:`/content/topics/boundaries` for the rules, the ratchet, and the modes.
   :doc:`system-checks` for the condition of every code.
   :doc:`management` for ``manage.py next boundaries``.
   :doc:`settings` for ``BOUNDARIES``.
