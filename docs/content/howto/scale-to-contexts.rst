.. _howto-scale-to-contexts:

Scale a project to contexts
===========================

Problem
-------

A project has grown past a dozen applications, and several teams change it at once.
A flat list in ``INSTALLED_APPS`` no longer says which applications belong together, and which arrows between them are intended.
The project should stay one Django project with one deployment, while each group of applications grows as if it could leave.

Solution
--------

Group the applications into contexts, plain Python packages, and keep every application an ordinary Django application inside them.
Declare the dependencies of each application through ``depends_on``, and isolate the one that grows towards a deployment of its own.
Turn the boundary checks on with ``NEXT_FRAMEWORK["BOUNDARIES"]`` and run them in CI with ``manage.py check --tag boundaries``.
next.dj checks what the framework ties by name, page trees, DI keys, foreign keys, zones, and permissions, and a linter of the import graph, if the project wants one, checks contracts on the plain imports between packages.

Walkthrough
-----------

Group applications into contexts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A context is a package that holds applications and no code of its own, and next.dj has no notion of it, as :ref:`topics-project-layout` describes under *Contexts*.

.. code-block:: text
   :caption: a project grouped into contexts

   ledger/
     __init__.py
     core/
       __init__.py
       apps.py
       money.py
     identity/
       __init__.py
       accounts/
     money/
       __init__.py
       wallets/
         __init__.py
         apps.py
         models.py
         services.py
         pages/
       operations/
     reporting/
       __init__.py
       ledgers/

An application inside a context names its full dotted path and sets its ``label`` explicitly, since two contexts may each hold an application of the same short name.
The prefix of its pages carries the context, so the URL space follows the grouping.

.. code-block:: python
   :caption: ledger/money/wallets/apps.py

   from django.apps import AppConfig

   class WalletsConfig(AppConfig):
       default_auto_field = "django.db.models.BigAutoField"
       name = "ledger.money.wallets"
       label = "wallets"
       mount = "money/wallets/"

``INSTALLED_APPS`` lists ``"ledger.money.wallets"``, and the boundary checks treat it like any other application, see :doc:`/content/howto/mount-an-application-under-a-prefix` for the prefix.

Turn the checks on
~~~~~~~~~~~~~~~~~~

The boundary checks stay off until the settings name a mode, and they run only under their own tag.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "BOUNDARIES": "strict",
   }

.. code-block:: bash
   :caption: shell

   uv run python manage.py check --tag boundaries --fail-level WARNING

A plain ``manage.py check`` and ``runserver`` leave them out, so the step belongs in CI, see :ref:`topics-boundaries` under *Running the checks*.

Declare the dependencies
~~~~~~~~~~~~~~~~~~~~~~~~

Turn the declarations on once the arrows between the applications are the ones the project wants.
``sync`` reads the graph and prints the ``depends_on`` each application needs, and ``--add`` writes it.

.. code-block:: bash
   :caption: shell

   uv run python manage.py next boundaries sync
   uv run python manage.py next boundaries sync --add

Read the diff before writing it, because it records every arrow there is, the unwanted ones included.
Cut an unwanted edge first, and write the tuple after.
From then on ``next.W150`` reports a new arrow that the tuple leaves out, so adding a dependency takes a reviewed change to an ``apps.py``.
Add ``manage.py next boundaries --exact`` to CI as well, so an entry the code no longer uses fails the build until someone removes it, see :ref:`topics-boundaries-sync` and :ref:`topics-boundaries-exact`.

Isolate an application that grows
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An application that may one day run as a service of its own, or move to another database, sets ``isolated = True``.
Its neighbours then import only its ``services`` and ``signals``, keep no foreign key to its models, and receive frozen dataclasses rather than models from its services.

.. code-block:: python
   :caption: ledger/money/wallets/apps.py

   from django.apps import AppConfig

   class WalletsConfig(AppConfig):
       default_auto_field = "django.db.models.BigAutoField"
       name = "ledger.money.wallets"
       label = "wallets"
       mount = "money/wallets/"
       depends_on = ("ledger.core", "ledger.identity.accounts")
       isolated = True

The findings it raises on a living application are warnings that ``manage.py next boundaries --refreeze`` can record, so isolation starts with the current debt and the ratchet keeps it from growing, see :ref:`topics-boundaries-isolated`.
Isolate one application at a time, the one whose independence pays for the dataclasses, and leave the rest with the three public modules.

Keep the shared kernel small
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``ledger.core`` holds what every context needs, the money amount and the identifiers, and nothing a single context owns.
It is a shared kernel in the vocabulary of Eric Evans, the part of the model the teams agree to share, kept small and changed only with the consent of the contexts that use it.
``manage.py next boundaries`` prints its modules, its lines, and the number of its users on every run, see :ref:`topics-boundaries-shared-kernel`.

``ledger.reporting`` plays the opposite role.
It is a conformist in the same vocabulary, a context that takes the models of the others as they are and reads the ``QuerySet`` methods of ``operations`` directly, without a translation of its own.
That is legal under the boundary rules, since an import of a ``models`` module along a declared arrow is an edge like any other, as long as the context it reads is not isolated.
A conformist sits at the top of the layers, so nothing depends on it and its reads change whenever the models they follow change.

Recipes for import linters
~~~~~~~~~~~~~~~~~~~~~~~~~~

next.dj ships no integration with a linter of the import graph, and the recipes below run such a linter beside the boundary checks.
A linter of the import graph reads packages with an ``__init__.py``, so a page tree, whose directories carry none, is invisible to it, and so are DI keys, foreign keys declared by a string, zones, components, and permissions.
next.dj reads all of those, and its rules know the roles of the modules of an application, while it holds no contract about layers or about who may import a kernel.
A project that wants both runs both, ``manage.py check --tag boundaries`` for the framework and the linter for the packages.

import-linter
^^^^^^^^^^^^^

`import-linter <https://import-linter.readthedocs.io/>`__ checks contracts on the import graph of Python packages, and from version 2.6 it offers every contract below.
The configuration lives in ``pyproject.toml`` under ``[tool.importlinter]``.

.. code-block:: toml
   :caption: pyproject.toml

   [tool.importlinter]
   root_package = "ledger"

   [[tool.importlinter.contracts]]
   name = "Contexts and the apps inside them import each other one way"
   type = "acyclic_siblings"
   ancestors = ["ledger"]

   [[tool.importlinter.contracts]]
   name = "Contexts sit in layers, reporting on top and the kernel at the bottom"
   type = "layers"
   containers = ["ledger"]
   layers = ["reporting", "money", "identity", "core"]

   [[tool.importlinter.contracts]]
   name = "Only the contexts that agreed to it use the shared kernel"
   type = "protected"
   protected_modules = ["ledger.core"]
   allowed_importers = ["ledger.identity", "ledger.money"]

``acyclic_siblings`` forbids a cycle between the children of ``ledger`` and, one level down, between the applications of each context.
``layers`` lets a context import only the contexts below it.
``protected`` turns the users of the kernel into a list, so a new user of ``ledger.core`` changes ``pyproject.toml`` and goes through review.

.. code-block:: bash
   :caption: shell

   uvx --from "import-linter>=2.6" lint-imports --no-cache

``--no-cache`` keeps import-linter from writing ``.import_linter_cache/`` into the project.

A project without contexts lists its applications as root packages instead.
For the three applications of the ``admin`` example, a base and two plugins on top of it, the same contracts read as follows.

.. code-block:: ini
   :caption: .importlinter

   [importlinter]
   root_packages =
       shadcn_admin
       library
       admin_audit

   [importlinter:contract:plugins]
   name = The plugin apps sit on top of the admin and never import each other
   type = layers
   layers =
       admin_audit | library
       shadcn_admin

   [importlinter:contract:admin-internals]
   name = Only the admin itself imports its internal modules
   type = protected
   protected_modules =
       shadcn_admin.context_processors
       shadcn_admin.deps
       shadcn_admin.forms
       shadcn_admin.permissions
       shadcn_admin.utils
   allowed_importers =
       shadcn_admin

``admin_audit | library`` puts the two plugins on one layer, independent of each other, and ``protected`` keeps the internals of ``shadcn_admin`` to ``shadcn_admin`` itself.
``lint-imports --config .importlinter --no-cache`` runs it from the directory of the project.

tach
^^^^

`tach <https://github.com/gauge-sh/tach>`__ declares the modules of a project and the dependencies each may have in a ``tach.toml``, and ``tach check`` fails on an import outside them.
``tach sync`` writes the dependencies the code already has, much as ``manage.py next boundaries sync`` writes ``depends_on``, and ``tach mod`` picks the modules interactively.
Its own documentation describes the format of ``tach.toml``, and its modules map onto the applications or the contexts of the project the same way the contracts above do.

Ban the hidden imports in page trees
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``apps.get_model`` and ``import_string`` reach a module without an import statement, so no linter of the import graph sees the edge.
Ruff bans them with ``banned-api``, and a negated pattern under ``per-file-ignores`` keeps the ban to the directories of the page trees, the one place where a lookup by name has no business.

.. code-block:: toml
   :caption: pyproject.toml

   [tool.ruff.lint]
   extend-select = ["TID251"]

   [tool.ruff.lint.flake8-tidy-imports.banned-api]
   "django.apps.apps.get_model".msg = "A page tree imports the model from the models module of its app."
   "django.utils.module_loading.import_string".msg = "A page tree imports what it uses by name."

   [tool.ruff.lint.per-file-ignores]
   "!**/{pages,*_pages}/**" = ["TID251"]

The pattern names the directories that hold a page tree, ``pages`` for the default ``PAGES_DIR`` and ``*_pages`` for second trees named that way, such as ``book_pages`` or ``auth_pages``.
A tree of another name, such as an ``operations/`` tree that ``mounts`` maps, falls outside the pattern, so the project adds its directory name to the braces, as long as no package of the project shares that name.
``ruff check`` then reports both calls in every file of those trees and leaves ``services.py`` and the rest of the project alone.
``next.W140`` already reports a ``get_model`` that names a model of another application, and the ban goes further, to every lookup by name, the own application included.

Where the pattern stops
~~~~~~~~~~~~~~~~~~~~~~~

Moving a model between applications moves it between migration histories, so a context split once the boundaries are known costs a data migration, and one split too early costs it twice.
A foreign key between contexts makes their migrations depend on each other, which is why an isolated application keeps the id rather than the key.
A table that every context reads on every request does not leave its context through services without a cost, since a join becomes several queries, and such a table belongs in the kernel or stays shared on purpose.
A report that joins across isolated contexts reads from a model of its own, filled through signals, rather than through the services one row at a time.

Verification
------------

Run the boundary checks and the exact comparison.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check --tag boundaries --fail-level WARNING
   uv run python manage.py next boundaries --exact

The first prints ``System check identified no issues``, the second ``Every depends_on entry is used.``, and both exit with status zero.
A linter from the recipes above reports its own result beside them.
``manage.py next boundaries <app>`` prints ``Isolated: yes`` for an isolated application.

See also
--------

.. seealso::

   :doc:`/content/topics/boundaries` for the rules behind each declaration.
   :doc:`/content/topics/project-layout` for the project sizes and the contexts.
   :doc:`/content/ref/management` for ``next boundaries``.
   :doc:`/content/deployment/split-deployments` for several settings over one code base.
   :doc:`publish-events-reliably` for signals between contexts that must not lose an event.
