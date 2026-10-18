.. _ref-boundaries:

Boundaries reference
====================

Module summary
--------------

``next.boundaries`` builds the graph of edges between the applications of a project, judges it against the boundary rules and the ``boundaries.toml`` ratchet, and reports the result as system checks under the ``boundaries`` tag.
The package exports ``App``, ``AstImportGraph``, ``BaselineError``, ``BoundariesManager``, ``Change``, ``Debt``, ``Declaration``, ``Declarations``, ``Edge``, ``EdgeKind``, ``Finding``, ``Graph``, ``GrimpImportGraph``, ``ImportGraphBackend``, ``ImportGraphError``, ``ImportScanError``, ``Kernel``, ``Plan``, ``RULES``, ``Refrozen``, ``Report``, ``Rule``, ``Scan``, ``SyncError``, ``Tangle``, ``UnknownAppError``, ``Unused``, ``boundaries_manager``, and ``boundary_checks``, plus the ``signals`` module.
It exports the result types this page documents and leaves the functions that produce them in their modules.
Every name but the exceptions and ``signals`` loads on first use, so importing ``next.signals`` loads neither the scan engine nor grimp.
``next.boundaries.checks`` registers ``check_boundaries``, ``check_declarations``, and ``check_boundaries_mode`` in ``boundary_checks``, a ``CheckRegistry`` of its own rather than Django's global one, and ``next.boundaries`` exposes that registry as ``boundary_checks``, while ``next.checks`` re-exports the three checks.
The package loads the registry on first use, so ``manage.py check`` without the tag loads neither the scan engine nor grimp.
The ``check`` command of the ``next`` application lends them to the global registry for one run while ``--tag boundaries`` or ``--list-tags`` asks, so no other command runs them, see :ref:`topics-boundaries` under *Running the checks*.
grimp is an optional dependency that the ``boundaries`` extra installs, and the package imports and scans without it.
:doc:`/content/topics/boundaries` covers the rules and the workflow.

Check codes
-----------

Every boundary code but ``next.E172`` is built from one row of ``RULES``, which holds its level, its hint, and the section of :doc:`/content/topics/boundaries` its hint points to.
``check_declarations`` builds ``next.E174`` and ``next.E175`` from the ``AppConfig`` of each application of the project, the installed applications under ``BASE_DIR``, and ``check_boundaries`` builds the rest.
``next.E172`` belongs to the ``NEXT_FRAMEWORK`` checks of ``next.conf.checks`` and carries the ``next`` tag, and ``check_boundaries_mode`` reports it under ``boundaries`` too, while the rest carry ``boundaries`` alone.
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
     - Warning under ``"warn"``, Error under ``"strict"``
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
   * - ``next.E174``
     - Error
     - No
     - :ref:`topics-boundaries-closure`
   * - ``next.E175``
     - Error
     - No
     - :ref:`topics-boundaries-declarations`
   * - ``next.E176``
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
   * - ``next.W150``
     - Warning
     - Yes
     - :ref:`topics-boundaries-undeclared`
   * - ``next.W151``
     - Warning
     - Yes
     - :ref:`topics-boundaries-isolated-foreign-keys`
   * - ``next.W152``
     - Warning
     - Yes
     - :ref:`topics-boundaries-isolated-services`
   * - ``next.W153``
     - Warning
     - Yes
     - :ref:`topics-boundaries-annotate-services`

``Rule.level_in(mode)`` answers the level of a code in a mode, so ``next.E167`` keeps one code in both modes and changes only its level.
The message of a finding names the file relative to ``BASE_DIR`` and its line as the object of the check, and the hint closes with the full address of the section, such as ``https://next-dj.readthedocs.io/en/latest/content/topics/boundaries.html#no-cycles``.

The manager
-----------

``boundaries_manager`` is the one ``BoundariesManager`` the check and ``manage.py next boundaries`` share.
``scan()`` builds the graph, the DI keys, and the findings on first use and keeps them for the rest of the check run, keyed on the router manager of the run like the run memos of ``next.checks.common``, and ``reset()`` drops the held scan.
``graph()`` and ``findings()`` read that scan, and ``report()`` judges its findings against ``boundaries.toml`` and the mode.
``refreeze()`` writes ``boundaries.toml`` and sends ``baseline_refrozen``.
When ``boundaries.toml`` records a file on disk that no installed application holds, or a ``depends_on`` names an application the settings do not install, ``report()`` judges only growth and ``refreeze()`` keeps the records of files on disk, see :doc:`/content/deployment/split-deployments`.
``refreeze()`` without ``BASE_DIR`` raises ``BaselineError`` first.
``enabled`` answers whether ``NEXT_FRAMEWORK["BOUNDARIES"]`` turns the boundaries on, and while it is false both checks report nothing.
``mode`` answers the mode the key names, ``"warn"`` for any value but ``"strict"``, and ``strict`` whether that mode is ``"strict"``.
``disabled()`` answers why the strict mode goes unchecked, ``None`` while nothing stops it and a ``next.W148`` finding without ``BASE_DIR``, and ``check_boundaries`` reports exactly that finding in place of the scan.

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

AppConfig attributes
--------------------

Two optional class attributes of an ``AppConfig`` declare the boundaries of its application, and ``check_declarations`` refuses any other shape with ``next.E175``.

.. list-table::
   :header-rows: 1
   :widths: 15 25 60

   * - Attribute
     - Shape
     - Meaning
   * - ``depends_on``
     - A tuple of application names, ``()`` included
     - The applications this one may depend on, each by its dotted ``name``.
       ``next.W150`` reports an edge to an application of the project the tuple leaves out, and ``next.E174`` an entry the current settings do not install.
   * - ``isolated``
     - A ``bool``
     - Whether the application publishes only its ``services`` and ``signals``, which turns on ``next.W151``, ``next.W152``, and ``next.W153``.

An attribute the class leaves out declares nothing, and so does a refused one.
Only the applications of the project are read, those installed from under ``BASE_DIR``, so a third-party ``AppConfig`` that carries an attribute of the same name with a meaning of its own is left alone, and a project without ``BASE_DIR`` declares nothing.
The attributes are read from the configuration class Django installs, inherited ones included, see :ref:`topics-boundaries-declarations`.

.. code-block:: python
   :caption: admin_audit/apps.py

   from django.apps import AppConfig

   class AdminAuditConfig(AppConfig):
       name = "admin_audit"
       label = "admin_audit"
       default_auto_field = "django.db.models.BigAutoField"
       depends_on = ("shadcn_admin",)
       isolated = True

Declarations
------------

``Declaration`` holds the checked attributes of one application of the project, ``depends_on`` being ``None`` when the application declares none or declares it wrongly.
``Declarations`` holds every ``Declaration`` with the ``next.E174`` and ``next.E175`` findings, answers one application through ``of(label)``, and the labels of the isolated ones through ``isolated``.
``Scan.declarations`` keeps the ``Declarations`` of the scan, so ``next.W150`` and the isolation rules read the same values the check reports on.
``Unused`` is what ``manage.py next boundaries --exact`` compares, the applications that declare ``depends_on`` and each entry that names an application of the project no edge leads to.

.. autoclass:: next.boundaries.Declaration
   :members:

.. autoclass:: next.boundaries.Declarations
   :members:

.. autoclass:: next.boundaries.Unused
   :members:

Sync and kernels
----------------

``Plan`` is what ``sync`` would do, its ``changes`` and the labels it ``skipped``, the applications without a configuration class of their own in the project.
``Change`` is one file that ``manage.py next boundaries sync`` rewrites, with its ``path``, its text ``before`` and ``after``, and ``diff(project)``, the unified diff the command prints.
``Kernel`` is the size of one shared kernel, its ``label``, its ``modules``, its non-empty ``lines``, and the number of ``users``, the applications with an edge to it.

.. autoclass:: next.boundaries.Plan
   :members:

.. autoclass:: next.boundaries.Change
   :members:

.. autoclass:: next.boundaries.Kernel
   :members:

Import graph backends
---------------------

``ImportGraphBackend`` reads the modules of the top packages of the project applications and the imports between them through one method, ``scan(project)``.
It returns an ``ImportScan`` of ``next.boundaries.imports``, a frozenset of dotted ``modules`` and a tuple of ``Import`` values, each with its ``importer``, its ``imported`` module, and its ``line``.
``GrimpImportGraph`` reads through grimp and refuses to start without it, and ``AstImportGraph`` reads the syntax of each module the way grimp does, imports inside functions and under ``TYPE_CHECKING`` included.
Both take the target of an import as the module it names when that module exists, else its direct parent, and drop it otherwise, so ``import a.x.nothing`` reads ``a.x`` while ``import a.x.nothing.deeper`` reads nothing, and a module file ``x.py`` wins over a package ``x/`` beside it.
The ``ast`` reader reads the bytes of each source, a byte order mark and a coding cookie included, the way Python does, while grimp reads UTF-8 alone and answers another encoding with ``next.E173`` and a hint to save the file as UTF-8 or name ``AstImportGraph``.
``next.boundaries.backends.import_graph()`` builds the backend ``NEXT_FRAMEWORK["BOUNDARIES_IMPORT_GRAPH"]`` names, grimp when the key is ``None`` and grimp is installed, and the ``ast`` reader otherwise.
A backend of the project subclasses ``ImportGraphBackend``, and the key names it by its dotted path.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "BOUNDARIES": "strict",
       "BOUNDARIES_IMPORT_GRAPH": "next.boundaries.AstImportGraph",
   }

.. autoclass:: next.boundaries.ImportGraphBackend
   :members:

.. autoclass:: next.boundaries.GrimpImportGraph

.. autoclass:: next.boundaries.AstImportGraph

Exceptions
----------

``ImportScanError`` says the backend could not build the import graph, keeping the ``reason``, the ``file``, and the ``line``.
``ImportGraphError``, an ``ImproperlyConfigured``, refuses the backend ``BOUNDARIES_IMPORT_GRAPH`` names, keeping the ``reason``, and the check reports it as ``next.E176``.
``BaselineError`` refuses a ``boundaries.toml`` that cannot be read or written, keeping the ``path`` and the ``reason``.
``UnknownAppError`` names a label or a name that is no application of the project, keeping it as ``key``.
``SyncError`` refuses a ``depends_on`` that ``sync`` cannot rewrite by itself, keeping the ``file``, the ``line``, and the ``reason``.
``manage.py next boundaries`` turns each of them into a ``CommandError``.

.. autoexception:: next.boundaries.ImportGraphError

.. autoexception:: next.boundaries.ImportScanError

.. autoexception:: next.boundaries.BaselineError

.. autoexception:: next.boundaries.UnknownAppError

.. autoexception:: next.boundaries.SyncError

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
   :doc:`settings` for ``BOUNDARIES`` and ``BOUNDARIES_IMPORT_GRAPH``.
