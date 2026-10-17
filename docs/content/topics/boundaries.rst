.. _topics-boundaries:

Boundaries
==========

A next.dj project grows as a set of Django applications, and the boundaries between them decide how far a change in one of them reaches.
next.dj reads the sources of the project, builds a graph of what each application takes from another, and reports every edge that breaks the layout as a system check.
The checks run with ``manage.py check``, so the structure of the project is tested on every run the way a test suite tests its behaviour.
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
It renders the components of its neighbour through ``{% component %}`` and asks for the named dependencies of its neighbour through ``Depends("name")``.
Its page tree holds delivery only, so the tree reads rows through ``visible_to`` and writes through a form or a function of ``services.py``.
A signal replaces an import only where the import would close a cycle.

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

The import edges come from grimp, which builds the graph of every import between the modules of the project packages.
grimp reads an import inside a function and one under ``if TYPE_CHECKING`` as well, so a deferred import and an import kept for type hints are edges like any other.
A page tree is no package, since its directories carry no ``__init__.py`` and a name such as ``[int:id]`` is no Python identifier, so next.dj adds the import statements of those files from their syntax, and does the same for a directory without ``__init__.py`` anywhere in an application.
A file with a syntax error stops the scan with ``next.E173``, a file of a page tree included, and no other boundary check runs until it is fixed, because every rule reads the graph the error leaves unbuilt.
A relative import above its package in a file grimp reads stops the scan the same way, and the scan names it before grimp runs.

The rules
---------

Each rule is a system check with a code of its own, and its message carries a hint and the address of its section on this page.
A warning names the file and the line it was found at, and so does ``next.E167``.
:doc:`/content/ref/boundaries` lists every code with its level.

.. _topics-boundaries-no-cycles:

No cycles
~~~~~~~~~

Applications that reach one another through any chain of edges form a cycle, and ``next.E167`` reports it as an error in every mode.
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

The boundary checks carry the ``boundaries`` tag and no other, the importable constant ``next.checks.BOUNDARIES``.
``manage.py check`` runs them with every other check, and so does every command that runs the checks first, such as ``runserver`` and ``migrate``, so an error such as a cycle stops those commands too.
``manage.py check --tag next`` runs the framework checks without them, and ``manage.py check --tag boundaries`` runs them alone.
A warning leaves the exit status at zero, so a CI step that holds the line runs ``manage.py check --tag boundaries --fail-level WARNING``, see :doc:`/content/deployment/checklist`.
The scan reads the sources once per check run and keeps the result for the rest of the run.

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
A ``boundaries.toml`` that cannot be read or breaks the format is ``next.E170``.
Every warning but ``next.W148`` can be recorded, and so can ``next.E167``, while an error about the record, the mode, or the scan cannot.
:doc:`/content/ref/boundaries` describes the format in full.

Only ``manage.py next boundaries --refreeze`` writes the file, and a check never does.
Without a file the command records every finding there is, which is how a project starts its ratchet.
With a file it only shrinks each record to what the file still holds, drops the records that reach zero, and records no new finding, so it prints how many findings it left unrecorded.
A new finding is fixed, or the file is deleted and frozen again, which shows in review as a change of ``boundaries.toml``.
A checkout without the file, such as a CI run of a project that never froze one, counts every finding in full.

``manage.py next boundaries`` prints the debt per application and code, found against recorded, and starts without the system checks, so it answers even while ``manage.py check`` fails, see :doc:`/content/ref/management`.

.. _topics-boundaries-strict-mode:

Strict mode
-----------

``NEXT_FRAMEWORK["BOUNDARIES"]`` picks one of two modes.
``"warn"``, the default, reports a finding the ratchet does not record as a warning and leaves the exit status of ``manage.py check`` alone.
``"strict"`` adds ``next.E171`` while such a warning remains, one error that counts them, so the run fails until each one is fixed or recorded.
The errors fail the run in both modes.
A new project starts in strict mode, which the project template sets, and an existing project switches once its debt is recorded.
A value outside the two is ``next.E172``, and the checks run as under ``"warn"`` until it is fixed.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "BOUNDARIES": "strict",
   }

.. _topics-boundaries-installing:

Installing
----------

The import graph needs grimp, which next.dj installs with the ``boundaries`` extra.

.. code-block:: bash
   :caption: shell

   uv add "next.dj[boundaries]"
   # or
   pip install "next.dj[boundaries]"

Without grimp the boundary checks are off.
In the ``"warn"`` mode ``manage.py check`` says so once with the info message ``next.I001``, which fails no run.
In the ``"strict"`` mode a missing grimp or a missing ``BASE_DIR`` is ``next.W148``, a warning rather than an error, so a fresh project without the extra still runs ``runserver`` while a CI run with ``--fail-level WARNING`` fails.
``manage.py next boundaries`` stops with a ``CommandError`` in either case.

What the checks do not see
--------------------------

The checks read the source code, the templates as text, and the registries that the start of Django fills, so they see what the code states and nothing it computes.

- A package of the project without an ``AppConfig``, such as a ``lib/`` or ``core/`` beside the applications, is no node, and an edge through it stays invisible, so shared code becomes an application of its own, a shared kernel with an ``AppConfig``.
- ``signal.connect(...)`` in place of ``@receiver`` leaves no ``signal`` edge.
- ``include("billing.urls")`` in a URLconf leaves no edge.
- ``import_string``, ``import_module``, ``__import__``, and a ``getattr`` on a module leave no edge.
- ``Depends`` imported under another name, and a ``get_model`` or a ``Depends`` whose argument is no literal, leave no edge.
- A zone or a component named by a variable leaves no edge.
- The ratchet counts findings per file and code, so fixing one finding while adding another of the same code in the same file goes unnoticed.

A clean run is advice about structure and never access control.
What a user may read and do is decided at runtime, by ``visible_to`` on the rows a page loads and by ``has_perm`` with the predicates of ``permissions.py``, see :doc:`permissions`.

See also
--------

.. seealso::

   :doc:`/content/ref/boundaries` for the codes, the API, and the format of ``boundaries.toml``.
   :doc:`project-layout` for the files of an application and the project sizes.
   :doc:`application-layout` for where models, querysets, services, and signals hold the logic.
   :doc:`/content/ref/management` for ``manage.py next boundaries`` and the Module Canvas.
