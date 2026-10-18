.. _topics-boundaries:

Boundaries
==========

A next.dj project grows as a set of Django applications, and the boundaries between them decide how far a change in one of them reaches.
next.dj reads the sources of the project, builds a graph of what each application takes from another, and reports every edge that breaks the layout as a system check.
A project turns the checks on with ``NEXT_FRAMEWORK["BOUNDARIES"]`` and runs them with ``manage.py check --tag boundaries``, in CI and in the test of the project template, so the structure of the project is tested the way a test suite tests its behaviour.
:doc:`project-layout` names the files of an application, and :doc:`application-layout` decides which of them a piece of logic belongs in.

.. contents::
   :local:
   :depth: 2

The unit is the application
---------------------------

Each node of the graph is an application of the project, an entry of ``INSTALLED_APPS`` whose directory lies under ``BASE_DIR``.
next.dj, Django, and an application installed as a distribution are dependencies rather than nodes, so an import of them is never an edge.
The scan reads every Python file and template of an application and leaves out exactly ``tests/``, ``tests.py``, and ``migrations/`` at its root, plus the directory of an application nested inside it, so a ``tests_helpers.py`` is scanned like any other module.
A project without ``BASE_DIR`` in its settings has no nodes, which the strict mode reports, see `Installing`_.

An application publishes three modules, ``models``, ``services``, and ``signals``, and another application imports from those three alone.
An isolated application publishes the last two only, see :ref:`topics-boundaries-isolated`.
It renders the components of its neighbour through ``{% component %}`` and asks for the named dependencies of its neighbour through ``Depends("name")``.
Its page tree holds delivery only, so the tree reads rows through ``visible_to`` and writes through a form or a function of ``services.py``.
A signal replaces an import only where the import would close a cycle.

Why a policy of its own
-----------------------

A hook for an outside linter would leave every project to invent the same rules and its own words for them.
next.dj ships one vocabulary for an application instead, three public modules, ``depends_on``, and ``isolated``, and each finding carries a code that links to its paragraph on this page.
It keeps a ratchet with a count of the debt per file and code, so a project that starts late measures what it owes and pays it off.
It reads the edges Django and next.dj make without an import statement, foreign keys, ``get_model``, DI keys, signals, zones, components, and permissions, which a linter of the import graph never sees.
A linter of plain imports stays useful beside it, and :doc:`/content/howto/scale-to-contexts` gives recipes for import-linter, ruff, and tach.

Edges
-----

An edge says that one application depends on another, and it keeps the file and the line it was found at.
Eight kinds of edge exist.
None of them is a finding on its own, the ``component`` and ``perm`` kinds included, but every kind counts towards a cycle and appears in the Module Canvas.

.. list-table::
   :header-rows: 1
   :widths: 14 86

   * - Kind
     - Found at
   * - ``import``
     - An import statement of one application that reads a module of another, from the graph described under `Import graph`_.
   * - ``fk``
     - A relation field of a model, such as a ``ForeignKey`` or a ``ManyToManyField``, whose target is a model of another application.
   * - ``get_model``
     - A ``get_model`` call whose string literals, positional or ``app_label=`` and ``model_name=``, name a model of another application.
   * - ``signal``
     - A ``@receiver`` that listens to a signal of another application, or names a sender of it by the model or by a ``"label.Model"`` literal.
   * - ``di``
     - A ``Depends("key")`` or ``Depends(dependency="key")`` whose key a provider of another application registers, and a bare ``Depends()`` whose key is the name of its parameter.
   * - ``zone``
     - A ``.refresh(zone="name")`` call whose zone only pages of another application declare.
   * - ``component``
     - A ``{% component "name" %}`` or ``{% #component "name" %}`` in a template of one application, a ``.djx`` or ``.html`` file or a module-level ``template = "..."`` literal, whose component another application registers and this one does not.
   * - ``perm``
     - A permission literal ``"label.codename"`` whose label is another application, in ``has_perm(...)``, ``permission_required(...)``, ``add_perm(...)``, a ``permission_required=`` keyword of any call such as ``@action(...)``, a ``Meta.permission_required``, or a ``{% has_perm %}`` tag.

A link is no edge.
``{% url %}`` and ``reverse`` to a page of another application name an address rather than code, so the linked application can change everything behind the address without touching the one that links to it.
``manage.py next boundaries <app>`` prints the edges of one application grouped by its neighbours, see :doc:`/content/ref/management`.

.. _topics-boundaries-import-graph:

Import graph
~~~~~~~~~~~~

The import edges come from an import graph backend, which reads every import between the modules of the top packages of the applications.
Two backends ship, ``GrimpImportGraph`` on top of grimp and ``AstImportGraph``, a reader of the syntax of each module with no dependency, and a test of the framework holds them to the same modules, imports, and edges, with the target of an import taken by grimp's rule, the module itself or its direct parent.
``NEXT_FRAMEWORK["BOUNDARIES_IMPORT_GRAPH"]`` left at ``None`` picks grimp when it is installed and the ``ast`` reader otherwise, and a dotted path to a subclass of ``ImportGraphBackend`` picks that class, see :doc:`/content/ref/boundaries`.
A path that does not import, names no such subclass, or names ``GrimpImportGraph`` without grimp is ``next.E176``, and no other boundary check runs until it is fixed.

Both backends read an import inside a function and one under ``if TYPE_CHECKING``, so a deferred import and an import kept for type hints are edges like any other.
A page tree is no package, since its directories carry no ``__init__.py`` and a name such as ``[int:id]`` is no Python identifier, so next.dj adds the import statements of those files from their syntax, and does the same for a directory without ``__init__.py`` anywhere in an application.
The ``ast`` reader reads a byte order mark and a coding cookie as Python does, and grimp reads UTF-8 alone, so a source in another encoding under grimp is ``next.E173`` with a hint to save it as UTF-8 or name ``AstImportGraph``.
A file with a syntax error, invalid UTF-8 without a cookie included, stops the scan with ``next.E173``, a file of a page tree included, and no other boundary check runs until it is fixed, because every rule reads the graph the error leaves unbuilt.
A relative import above its package stops the scan the same way, and the scan names it before the backend runs.

The rules
---------

Each rule is a system check with a code of its own, and its message carries a hint and the address of its section on this page.
A warning names the file and the line it was found at, and so do ``next.E167``, ``next.E174``, and ``next.E175``.
:doc:`/content/ref/boundaries` lists every code with its level.

.. _topics-boundaries-no-cycles:

No cycles
~~~~~~~~~

Applications that reach one another through any chain of edges form a cycle, and ``next.E167`` reports it as a warning in the ``"warn"`` mode and as an error in the ``"strict"`` mode, under the same code.
Neither application of a cycle can change, be tested, or be removed without the other, so the group is one application in all but name.
The check reports one error per ordered pair of applications inside such a group that an edge joins, at the file and line of the first edge of the pair, and the message names the pair, every application of the group, and the shortest cycle through the first one as a witness.
A recorded ``next.E167`` therefore counts dependencies between applications inside the group, keyed on the file of the first edge of each pair.
A new edge between a pair already recorded adds no debt, since the dependency is the same, while a new pair or a new application joining the group is a new finding, which the ratchet reports as growth.
The usual cut moves the shared code down into an application both sides already depend on, or into a new one.
Where the lower application would have to call into the higher one, it declares a signal and sends it, and the higher one receives it, which turns that edge around, see `Receivers`_.

.. _topics-boundaries-public-modules:

Public modules
~~~~~~~~~~~~~~

An application imports another one only through its ``models``, ``services``, and ``signals`` modules, a package of the same name included, and ``next.W136`` reports an import of any other module.
``models`` is where Django applications meet through foreign keys and ``QuerySet`` methods, ``services`` is the write surface with the public reads, and ``signals`` holds what the application announces.
Everything else stays private, ``forms.py``, ``deps.py``, ``admin.py``, ``permissions.py``, the package root, and every helper module, so it can change without a search through the neighbours.
What another application needs from a private module moves into ``services.py``, or the code moves down into an application both sides depend on.

.. code-block:: python
   :caption: shop/services.py

   from billing.services import open_invoice

   def checkout(scope, *, order_id: int) -> None:
       open_invoice(scope, order_id=order_id)

.. _topics-boundaries-page-trees:

Page trees
~~~~~~~~~~

A file of a page tree or of a component folder at the root of an application, named by ``COMPONENTS_DIR`` of a component backend, belongs to the delivery of its application, and ``next.W137`` reports another application that imports it.
Such a file has no stable import path, as *Delivery stays in pages* in :doc:`project-layout` explains, and a template reaches a component of another application through the ``{% component %}`` tag rather than an import.
Code that two applications share moves into the ``services.py`` of the application that owns it.

.. _topics-boundaries-visible-to:

``visible_to``
~~~~~~~~~~~~~~

Every read of a page tree starts with the question of who is reading, so ``next.W138`` reports a ``.objects`` in a Python file of a page tree that is not followed directly by ``.visible_to(...)``.
``Note.objects.visible_to(scope).pinned()`` passes, while ``Note.objects.filter(pinned=True)`` does not.
A read that answers the same to everyone, such as a public list, moves into a function of ``services.py``, which the rule does not reach.
The rule reads the syntax, so a manager of a model the project does not own, such as ``ContentType.objects``, is reported too, and its read moves into a service in the same way.
:doc:`application-layout` shows the ``visible_to`` method of a custom ``QuerySet``, and ``DObject`` loads the row of a URL through the same method, see :ref:`topics-di-dobject`.

.. _topics-boundaries-writes:

Writes
~~~~~~

A page tree writes through a form or a function of ``services.py``, and ``next.W139`` reports a ``.save()`` called on anything else.
A save on an object whose name is a form by its words, ``form``, ``*_form``, ``form_*``, or ``*Form``, ``super().save()``, and ``self.save()`` in a method of a class whose name or base ends in ``Form`` pass, while a name that only holds the letters, such as ``platform``, is reported.
Setting an attribute on an object that the same function saves is reported as well, because the new value skips the validation of the form.
A write that spans several models or needs a transaction belongs to a service that the ``on_valid`` of the form calls, as :doc:`application-layout` shows.

.. _topics-boundaries-get-model:

``get_model``
~~~~~~~~~~~~~

``apps.get_model("billing", "Invoice")`` hides an edge from every reader of the import list, and ``next.W140`` reports a ``get_model`` call whose string literals name a model of another application.
The model comes from the ``models`` module of its application instead, or the neighbour answers the question through a function of its ``services.py``.
A migration keeps its ``get_model`` calls, since the scan leaves ``migrations`` out.

.. _topics-boundaries-di-keys:

DI keys
~~~~~~~

A named dependency is visible to the whole project, so its key tells which application provides it.
``next.W141`` reports a key whose first word, up to the first ``_`` or ``.``, is the label of another application, such as ``billing_plan`` provided from ``shop/deps.py``.
A key named after its own application or after no application passes, and so does ``scope``, the key the layout gives to the owner of the rows.
A ``Depends("billing_plan")`` in another application is an edge of kind ``di`` to the provider and never a finding, see :doc:`dependency-injection`.

.. _topics-boundaries-receivers:

Receivers
~~~~~~~~~

An application listens to its neighbours in its ``signals`` module, and ``next.W142`` reports a ``@receiver`` anywhere else that names a signal or a sender of another application.
One file then lists every reaction of the application, and the startup sweep that imports ``signals.py`` connects them with no ``AppConfig.ready`` code.
A signal exists to turn an edge around where an import would close a cycle, so a reaction that closes no cycle calls a function of the neighbour's ``services.py`` instead.

The same turn makes an extension point when a base application asks its plugins for something.
The base declares the signal and sends it from a function of its ``services.py``, every receiver returns a value, and the function collects the answers that :meth:`Signal.send <django.dispatch.Signal.send>` hands back.
In the ``admin`` example ``shadcn_admin`` declares ``nav_requested`` and ``panels_requested``, and ``admin_audit/signals.py`` answers with its sidebar link and its dashboard panel, so the base renders what it is given and never names the plugin, its permission, or its component.
:doc:`application-layout` shows a receiver that waits for the commit.

.. _topics-boundaries-sending-signals:

Sending signals
~~~~~~~~~~~~~~~

A signal announces a change, and the function of ``services.py`` that makes the change sends it, as ``archive_note`` does in :doc:`application-layout`.
``next.W143`` reports a ``send`` or ``send_robust`` of a signal that a ``signals.py`` of the project declares, made anywhere but the ``services`` module.
A page, a form, or the admin that delivers the change calls the service and leaves the send to it, so every way of delivering an operation announces it the same way.

.. _topics-boundaries-zones:

Zones
~~~~~

A zone belongs to the pages that declare it, and ``next.W144`` reports a ``.refresh(zone="name")`` whose zone only pages of another application declare.
Refreshing the zone of a neighbour ties the response of one application to the template of another.
The application refreshes only the zones of its own pages, and for the rest it listens to a signal of the other application.

.. _topics-boundaries-models:

Models
~~~~~~

Models live in ``models.py`` or a ``models`` package, the module Django looks in and the one other applications import, and ``next.W145`` reports a model class that the root ``__init__.py`` of an application defines.

.. _topics-boundaries-aggregators:

Aggregators
~~~~~~~~~~~

A module that re-exports the names of another application hides where they come from, and ``next.W146`` reports a ``from ... import *`` of another application and every name of another application that a literal ``__all__`` lists.
Each module imports the name from the application that owns it, right where it uses it, so its import list shows the real edge.

.. _topics-boundaries-file-names:

File names
~~~~~~~~~~

next.dj reads each role from one module, and ``next.W147`` reports a module at the root of an application whose name belongs to a role next.dj reads from another module.

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - Module
     - Belongs in
   * - ``providers.py``
     - ``deps.py``
   * - ``receivers.py``
     - ``signals.py``
   * - ``policy.py``, ``access.py``
     - ``permissions.py``
   * - ``facades.py``, ``selectors.py``, ``queries.py``
     - ``services.py``

*Names the layout avoids* in :doc:`project-layout` explains the choice.

.. _topics-boundaries-foreign-permissions:

Foreign permissions
~~~~~~~~~~~~~~~~~~~

A permission ``label.codename`` belongs to the application of its label, and its predicate lives in that application's ``permissions.py``.
``next.W149`` reports an ``add_perm`` whose name, positional or ``name=``, carries the label of another application, since it replaces the access policy of the owner from outside.
An application that needs a rule of its own registers it under its own label, and asking ``has_perm`` with the permission of a neighbour stays allowed as a ``perm`` edge.

Running the checks
------------------

The boundary checks carry the ``boundaries`` tag and no other, the importable constant ``next.checks.BOUNDARIES``, and they run only when a run asks for that tag.
``manage.py check --tag boundaries`` runs them, ``manage.py check --list-tags`` lists the tag, and ``manage.py next boundaries`` prints the debt they count.
A plain ``manage.py check``, ``--deploy``, and every command that runs the checks first, such as ``runserver``, ``migrate``, and ``test``, leave them out, so a finding never stops a server or a migration.
A warning leaves the exit status at zero, so a CI step that holds the line runs ``manage.py check --tag boundaries --fail-level WARNING``, see :doc:`/content/deployment/checklist`.

The checks are a static analysis of the code rather than a test of the configuration the process runs with, and the scan reads every file of the project, so they belong in CI and before a review rather than on the start of every process.
The ``next`` application keeps them in a registry of its own and overrides the ``check`` command, which lends them to the run of Django's system checks while ``--tag boundaries`` or ``--list-tags`` asks.
An application listed before ``next`` in ``INSTALLED_APPS`` that ships a ``check`` command of its own takes the command over, and ``manage.py check --tag boundaries`` then fails with Django's ``There is no system check with the "boundaries" tag.``, so ``next`` comes before such an application, while a ``check`` command of an application listed after ``next`` is shadowed without a word.
Application labels do not narrow ``manage.py check --tag boundaries``, because a cycle joins several applications and the ratchet and ``next.E171`` count the whole project, so ``manage.py next boundaries <app>`` prints one application instead.
A direct ``run_checks(tags=["boundaries"])`` goes past the command and returns an empty list, and ``next.boundaries.boundary_checks.run_checks()`` runs the checks from code.
The project template writes a test into its ``home`` application that runs ``manage.py check --tag boundaries --fail-level WARNING``, so ``manage.py test`` holds the boundaries of a new project as well.
The scan reads the sources once per check run and keeps the result for the rest of the run.
``next.E174`` and ``next.E175`` come from a second check that reads the ``AppConfig`` of each application of the project and nothing else, so they run without the scan.
It reads only the applications under ``BASE_DIR``, so a third-party ``AppConfig`` whose attributes share a name and mean something else is left alone, and without ``BASE_DIR`` it reports nothing.

.. _topics-boundaries-ratchet:

Ratchet
-------

A project that turns the checks on late already holds findings it cannot fix in one change.
``boundaries.toml`` in ``BASE_DIR`` records that debt per file and code, and a finding the file records leaves the output.
The record works as a ratchet, so the debt may shrink and never grow.

.. code-block:: toml
   :caption: boundaries.toml

   version = 1

   [[violation]]
   file = "library/admin.py"
   code = "next.W136"
   count = 1

A record covers exactly its count.
A file that holds more findings of the code than its record is ``next.E168``, which lists the lines of all of them, since the check cannot tell which one is new.
A file that holds fewer is ``next.E169``, so a fix lands together with the smaller record and the freed room cannot fill up again unnoticed.
Under settings that leave applications of the project out, which a recorded file of no installed application or a ``depends_on`` entry the settings do not install reveals, only growth is judged, see :doc:`/content/deployment/split-deployments`.
A ``boundaries.toml`` that cannot be read or breaks the format is ``next.E170``.
Every warning but ``next.W148`` can be recorded, ``next.E167`` included, whose record holds in the strict mode as well, where it is an error, while an error about the record, the mode, the scan, or a declaration cannot.
:doc:`/content/ref/boundaries` describes the format in full.

Only ``manage.py next boundaries --refreeze`` writes the file, and a check never does.
Without a file the command records every finding there is, which is how a project starts its ratchet.
With a file it only shrinks each record to what the file still holds, drops the records that reach zero, and records no new finding, so it prints how many findings it left unrecorded.
A new finding is fixed, or the file is deleted and frozen again, which shows in review as a change of ``boundaries.toml``.
A checkout without the file, such as a CI run of a project that never froze one, counts every finding in full.

``manage.py next boundaries`` prints the debt per application and code, found against recorded, and starts without the system checks, so it answers even while ``manage.py check --tag boundaries`` fails, see :doc:`/content/ref/management`.

.. _topics-boundaries-strict-mode:

Strict mode
-----------

``NEXT_FRAMEWORK["BOUNDARIES"]`` turns the boundaries on and picks one of two modes.
Without the key, ``None`` by default, the boundaries are off, both checks report nothing even under ``--tag boundaries``, and ``manage.py next boundaries`` answers with one line that names the key.
``"warn"`` reports a finding the ratchet does not record as a warning and leaves the exit status of ``manage.py check --tag boundaries`` alone.
``"strict"`` adds ``next.E171`` while such a warning remains, one error that counts them, so the run fails until each one is fixed or recorded.
The errors fail the run in both modes.
``next.E167`` is the one code whose level follows the mode, a warning under ``"warn"`` and an error under ``"strict"``, so an unrecorded cycle fails a strict run under its own code rather than through ``next.E171``.
A new project starts in strict mode, which the project template sets, and an existing project switches once its debt is recorded.
Any other value, a mistyped mode or ``True``, is ``next.E172`` and leaves the boundaries off until it is fixed, and the error shows once both in a plain ``manage.py check`` and under ``--tag boundaries``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "BOUNDARIES": "strict",
   }

.. _topics-boundaries-declared-dependencies:

Declared dependencies
---------------------

The graph shows what an application takes from its neighbours, and ``depends_on`` on its ``AppConfig`` states what it is meant to take.
The attribute is a tuple of application names, the dotted ``name`` of each application rather than its label, and it may name an application outside the project, such as ``django.contrib.auth``.
An application without the attribute declares nothing and the checks below leave it alone, so a project turns the declarations on one application at a time.
A new arrow between two applications then shows in the diff of an ``apps.py``, where a reviewer sees it, rather than deep inside a module.

.. code-block:: python
   :caption: library/apps.py

   from django.apps import AppConfig

   class LibraryConfig(AppConfig):
       name = "library"
       label = "library"
       verbose_name = "Library"
       depends_on = ("shadcn_admin",)

.. _topics-boundaries-undeclared:

Undeclared
~~~~~~~~~~

``next.W150`` reports an application that declares ``depends_on`` and has an edge of any kind to an application of the project the tuple leaves out.
One warning stands for each such pair, at the file and the line of the first edge, and its message counts the edges of each kind, such as ``fk x1, import x2``.
The edge is either declared, by adding the name to the tuple, or cut, as any other rule asks.
The warning can be recorded, so a project that declares late keeps its undeclared edges as debt, and the Module Canvas marks such a neighbour ``(undeclared)`` under ``Depends on``.

.. _topics-boundaries-exact:

``--exact``
~~~~~~~~~~~

The check never asks to remove a name, because a declared dependency that no code uses yet is a plan rather than a defect.
``manage.py next boundaries --exact`` compares the other way, lists each entry of ``depends_on`` that names an application of the project no edge leads to, and exits with status one while any is left.
A CI step that wants the declarations to match the code exactly runs it beside ``manage.py check --tag boundaries``.
An entry that names an application outside the project, such as ``django.contrib.auth``, has no node to compare and never counts.

.. _topics-boundaries-sync:

``sync``
~~~~~~~~

``manage.py next boundaries sync`` prints, as a unified diff, the names each application of the project needs in its ``depends_on`` to cover every edge it has, and ``--add`` writes them.
It only adds, keeps the order and the annotation of an existing tuple, and appends the new names in alphabetical order, so an entry the code no longer uses stays until a person removes it.
An application without the attribute gets one after its ``name``, ``label``, ``verbose_name``, or ``default_auto_field``, or in place of the ``pass`` or ``...`` that is the whole body of its class, and an application with no edges gets ``depends_on = ()``.
``manage.py next boundaries sync <app>`` limits the change to one application, named by its label or its dotted name.

.. code-block:: text
   :caption: manage.py next boundaries sync

   --- a/library/apps.py
   +++ b/library/apps.py
   @@ -7,4 +7,5 @@
        name = "library"
        label = "library"
        verbose_name = "Library"
   +    depends_on = ("shadcn_admin",)
        mounts: ClassVar[dict[str, str]] = {"book_pages": "library/book/<int:book>/"}
   Run with --add to write it.

``sync`` rewrites a literal tuple of strings and nothing else.
A value built with ``+``, a variable, an augmented assignment, an inherited ``depends_on``, a class body on the line of the class name, and a class below the top level of its module stop it with a ``CommandError`` that names the file, relative to ``BASE_DIR``, and the line to edit by hand.
Every new text is built before any file is written, so one refusal leaves every file as it was.
``--add`` writes each new text to a temporary file beside its original and replaces the originals only once all of them are written, so each file is swapped whole, and only an error of the operating system during the swap can leave a set of files half replaced, which the error then says by counting the files already written.

An application whose configuration class lives outside the applications of the project, a bare ``AppConfig`` or a class from another package, has no ``apps.py`` for ``sync`` to edit.
``sync`` skips such an application and names it in a closing note, and ``sync <app>`` for it stops with a ``CommandError`` that asks for an ``apps.py`` with an ``AppConfig`` subclass.
That is also how a bare ``AppConfig`` that Django installed in place of the intended class shows up, see :ref:`topics-boundaries-declarations`.
Rewriting a tuple that spans several lines drops the comments between its items and folds it onto one line when it fits in 88 characters.

.. _topics-boundaries-isolated:

``isolated``
------------

An application that grows towards a deployment of its own, or that one team wants to change without asking its neighbours, sets ``isolated = True`` on its ``AppConfig``.
Its public surface shrinks to ``services`` and ``signals``, so ``next.W136`` reports an import of its ``models`` from another application as well, and the message says the application is isolated.
A model or a queryset that its ``services`` re-exports does not get past the rule, since ``next.W136`` also reports a neighbour that imports such a name from the ``services`` of an isolated application.
The neighbours ask its services and hold plain values, so the application can reshape its tables, split them, or move them to another database without a search through the project.
The attribute takes a ``bool`` and nothing else.

.. code-block:: python
   :caption: admin_audit/apps.py

   from django.apps import AppConfig

   class AdminAuditConfig(AppConfig):
       name = "admin_audit"
       label = "admin_audit"
       default_auto_field = "django.db.models.BigAutoField"
       depends_on = ("shadcn_admin",)
       isolated = True

The three rules below are warnings that can be recorded, so isolation goes on a living application with its debt recorded, and the ratchet lets that debt shrink.
In the strict mode a finding nobody recorded fails the run through ``next.E171`` as any other warning does.

.. _topics-boundaries-isolated-foreign-keys:

Isolated foreign keys
~~~~~~~~~~~~~~~~~~~~~

``next.W151`` reports a relation field of another application, a ``ForeignKey``, a ``OneToOneField``, or a ``ManyToManyField``, whose target is a model of an isolated application.
A foreign key ties the table of the neighbour to the table of the isolated application, which can then no longer move or split that table alone.
The neighbour keeps the id of the record in a plain field and asks the services of the isolated application for the rest.
A relation from the isolated application to a model of another one is no finding.

The rule is a warning rather than an error because isolation starts on an application that already has such keys.
An error would demand that every key go in the change that sets ``isolated``, while a warning lets the ratchet record the keys there are and refuse every new one.

.. _topics-boundaries-isolated-services:

Isolated services
~~~~~~~~~~~~~~~~~

``next.W152`` reports a public function at the top level of the ``services`` module or package of an isolated application whose return annotation lets a model out.
A model or a queryset handed to a neighbour carries the table with it, its lazy relations and its ``save`` included, so the service returns data instead.
The check follows the annotation through imports and aliases and reports a model, abstract or concrete, a ``QuerySet``, a ``Manager``, ``Any``, ``object``, a class of the project that is neither a frozen slots dataclass nor an enum, and a name it cannot tell.
A model or a queryset inside ``Optional``, ``X | None``, ``list`` or another generic, and inside a string annotation, counts the same.
A bare container, such as ``list``, ``dict``, ``Iterable``, ``Sequence``, or ``Mapping`` from the builtins, ``typing``, or ``collections.abc``, and a bare ``django.core.paginator.Page`` say nothing about what they hold, so they count as a name the check cannot tell, while ``list[int]`` or ``Iterable[Entry]`` are read through their parameters.

A frozen dataclass with slots, an enum, ``TextChoices`` and ``IntegerChoices`` included, a builtin scalar such as ``int`` or ``str``, and a parametrised generic of them pass.
The dataclass is frozen so a caller cannot write through it, and its slots keep it to the declared fields.
The check reads the annotated fields of such a dataclass by the same rules, into every dataclass a field holds, so a model cannot hide one level down, and the message names the path, such as ``the dataclass iso.services.Outer, whose field rows holds the dataclass iso.services.Wrapped, whose field log holds the model iso.models.Record``.

.. code-block:: python
   :caption: admin_audit/services.py

   from dataclasses import dataclass
   from datetime import datetime

   from django.contrib.admin.models import LogEntry
   from django.contrib.contenttypes.models import ContentType
   from django.db.models import Model

   @dataclass(frozen=True, slots=True)
   class HistoryEntry:
       time: datetime
       message: str

   def object_history(obj: Model) -> list[HistoryEntry]:
       entries = LogEntry.objects.filter(
           content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk
       ).order_by("-action_time")
       return [
           HistoryEntry(time=entry.action_time, message=entry.get_change_message())
           for entry in entries
       ]

``object_history`` takes a model as an argument, which the rule allows, since it judges only what leaves the application.

The check reads syntax and the modules the process has already loaded, and it imports nothing.
It resolves the names of an annotation through the imports at the top level of the module, those under ``if``, ``try``, and their branches included, so an import inside a function does not count.
A public name bound to a function of the module, such as ``get = _get``, is read as that function, with the finding at the line of the assignment.
A name it cannot resolve, such as one from a star import, a ``TypeVar``, a nested class ``Outer.Inner``, or a class defined under ``if TYPE_CHECKING``, is reported as a name the check cannot tell, and the fix is an annotation it can follow through a plain import.
A type from a third-party module the process has not loaded passes, and a ``TypedDict`` or a ``NamedTuple`` of the project is reported as a class.
One finding stands per function, the first reason found, so fixing it may bring the next one up.

.. _topics-boundaries-annotate-services:

Annotate services
~~~~~~~~~~~~~~~~~

``next.W153`` reports a public function of the services of an isolated application without a return annotation, since the annotation is all the check reads.
A function whose name starts with an underscore is no public service and stays unread.

.. _topics-boundaries-closure:

Closure
-------

``next.E174`` reports an application whose ``depends_on`` names an application that the ``INSTALLED_APPS`` of the current settings leave out.
A project that ships several deployments from one code base, each with its own settings, holds every deployment closed this way, since a missing dependency there otherwise fails at the first request that reaches it, see :doc:`/content/deployment/split-deployments`.
The message names the settings module, or says ``these settings`` under ``settings.configure()``, and a label written in place of a name, such as ``auth`` for ``django.contrib.auth``, gets a message of its own, since ``depends_on`` takes names.
The hint asks for each entry to be the name of an application the settings install.
The check reads only the ``AppConfig`` of each application of the project, so it runs without the scan, while it needs ``BASE_DIR`` to tell which applications those are.

A foreign key into an application the settings leave out is Django's own finding.
A relation declared by a string such as ``"billing.Invoice"`` fails with ``fields.E300`` and ``fields.E307``, which say that the application is not installed, and a relation declared by an imported model class stops ``django.setup()`` with a ``RuntimeError`` before any check runs.
next.dj adds nothing to them, and they run in a plain ``manage.py check``, while ``next.E174`` runs under ``--tag boundaries`` like every boundary check.
CI therefore runs ``manage.py check --settings=...`` and ``manage.py check --tag boundaries --settings=...`` for each deployment, which covers foreign keys and declared dependencies together, see :doc:`django:ref/checks`.

.. _topics-boundaries-declarations:

Declarations
------------

``next.E175`` reports a boundary attribute of an ``AppConfig`` in a shape next.dj refuses, and the attribute then counts as never declared, so no other finding follows from it.
``depends_on`` takes a tuple of application names without an empty name, a repeated name, or the application itself.
``isolated`` takes a ``bool``.
The message points at the line of the attribute, on the base class when the configuration class inherits it.

The attributes count only on the class Django installs, and Django picks that class by itself when ``INSTALLED_APPS`` lists the package.
It looks at every ``AppConfig`` subclass in ``apps.py``, imported ones included, so an ``apps.py`` that imports a shared base configuration and subclasses it offers two candidates, and with neither marked as the default Django installs a bare ``AppConfig`` without a word.
The same happens when the base sets ``default = False``, since the subclass inherits it.
The attributes of the subclass then vanish with no ``next.E174`` or ``next.E175`` to say so, the Module Canvas shows no ``Isolated: yes``, and ``sync`` skips the application as one without a configuration class of its own.
The class that carries the attributes sets ``default = True``, or ``INSTALLED_APPS`` lists its dotted path, such as ``"shop.apps.ShopConfig"``, see :attr:`~django.apps.AppConfig.default`.

.. _topics-boundaries-shared-kernel:

Shared kernel
-------------

Code that several applications share, such as money amounts, identifiers, or a base model, lives in an application of its own whose dotted name ends in ``core``, such as ``ledger.core``.
next.dj treats such an application as a shared kernel in the sense of Eric Evans' *Domain-Driven Design*, a part of the model that several teams agree to share, and the book gives one rule for it, "keep this kernel small".
``manage.py next boundaries`` prints one line per kernel after its table, with the number of its modules, its non-empty lines, and the applications that use it, so its growth shows on every run.

.. code-block:: text
   :caption: manage.py next boundaries

   No boundary findings. Mode: strict.
   Shared kernel core: 4 modules, 120 lines, used by 3 apps. Keep it small, and change it with the consent of the apps that use it.

A kernel follows the rules of every application and no extra ones, and the line names the two habits that keep it a kernel.
It stays small, and it changes only with the consent of the applications that use it.

.. _topics-boundaries-installing:

Installing
----------

The checks need nothing beyond next.dj, since the ``ast`` reader builds the import graph without a dependency.
The ``boundaries`` extra installs grimp, which reads the graph of a large project faster and becomes the backend once it is installed.

.. code-block:: bash
   :caption: shell

   uv add "next.dj[boundaries]"
   # or
   pip install "next.dj[boundaries]"

The checks read the applications under ``BASE_DIR``, so a project without it has no nodes.
In the ``"strict"`` mode a missing ``BASE_DIR`` is ``next.W148``, a warning rather than an error, so a CI run with ``--fail-level WARNING`` fails, and ``manage.py next boundaries`` stops with a ``CommandError``.

What the checks do not see
--------------------------

The checks read the source code, the templates as text, and the registries that the start of Django fills, so they see what the code states and nothing it computes.

- A package of the project without an ``AppConfig``, such as a ``lib/`` or ``core/`` beside the applications, is no node, and an edge through it stays invisible, so shared code becomes an application of its own, a shared kernel with an ``AppConfig``, see :ref:`topics-boundaries-shared-kernel`.
- ``signal.connect(...)`` in place of ``@receiver`` leaves no ``signal`` edge.
- ``include("billing.urls")`` in a URLconf leaves no edge.
- ``import_string``, ``import_module``, ``__import__``, and a ``getattr`` on a module leave no edge.
- ``Depends`` imported under another name, and a ``get_model`` or a ``Depends`` whose argument is no literal, leave no edge.
- A zone or a component named by a variable leaves no edge.
- The ratchet counts findings per file and code, so fixing one finding while adding another of the same code in the same file goes unnoticed.
- An isolated application guards its imports, its foreign keys, and the return types of its services, so a DI provider of it that hands out a model, a receiver of its signals, a permission of its label, and a component of it rendered by a neighbour raise no isolation finding, only ``next.W150`` where the neighbour declares ``depends_on``.
- The services check reads the functions at the top level of the ``services`` module or package, and the public names bound to them, so a method, a ``lambda`` or a ``partial`` bound to a name, and a name the package re-exports from a module outside it stay unread.
- A model that a neighbour reaches as an attribute, through ``import iso.services as services`` and ``services.Record``, draws no ``next.W136``, which reads only names imported with ``from ... import``.
- ``next.E174`` checks that each name of ``depends_on`` is installed, and not that it names an application of the project.
- An application installed from outside ``BASE_DIR`` keeps its ``depends_on`` and ``isolated`` unread, so a reusable application checks its declarations in its own project.
- ``next.W150`` and ``--exact`` compare only applications of the project, so the declarations of an installed third-party application are never compared.

A clean run is advice about structure and never access control.
What a user may read and do is decided at runtime, by ``visible_to`` on the rows a page loads and by ``has_perm`` with the predicates of ``permissions.py``, see :doc:`permissions`.

See also
--------

.. seealso::

   :doc:`/content/ref/boundaries` for the codes, the API, and the format of ``boundaries.toml``.
   :doc:`project-layout` for the files of an application and the project sizes.
   :doc:`application-layout` for where models, querysets, services, and signals hold the logic.
   :doc:`/content/ref/management` for ``manage.py next boundaries`` and the Module Canvas.
   :doc:`/content/howto/scale-to-contexts` for declarations, isolation, and import contracts on a growing project.
