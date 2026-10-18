.. _ref-management:

Management commands
===================

Module summary
--------------

The ``next`` application ships three management commands.
``next`` dispatches the next.dj subcommands, ``routes``, ``permissions``, and ``boundaries``, ``showmetadata`` is a thin shell over the metadata of ``next.pages``, and ``check`` extends Django's command of the same name with the boundary checks.

check
-----

``manage.py check`` is Django's command with one addition, and ``manage.py help`` lists it under ``[next]``.
The boundary checks live in a registry of their own, ``next.boundaries.checks.boundary_checks``, and the command lends them to Django's registry for one run when ``--tag boundaries`` or ``--list-tags`` asks, then takes back exactly what it lent.
Every other run, a plain ``manage.py check``, ``--deploy``, and the checks that ``runserver``, ``migrate``, and ``test`` run first, never sees them.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check --tag boundaries --fail-level WARNING

Django takes a command from the first application in ``INSTALLED_APPS`` that ships it.
An application listed before ``next`` with a ``check`` command of its own hides this one, and ``--tag boundaries`` then fails loudly with Django's ``There is no system check with the "boundaries" tag.``, while such a command in an application listed after ``next`` is hidden by this one without a word.
Application labels after the command do not narrow ``--tag boundaries``, because the findings belong to the graph and the ratchet of the whole project, so ``manage.py next boundaries <app>`` answers for one application.
A run without the tag loads neither the scan engine nor grimp.

next
----

``manage.py next <subcommand>`` runs one subcommand, and ``manage.py next <subcommand> --help`` describes it.
Each module of ``next.management.subcommands`` whose name does not start with an underscore is a subcommand named after the module.

Django's own options, such as ``--settings`` and ``--skip-checks``, belong to ``next`` and go before the subcommand name, because the parser of a subcommand does not know them.
Like any Django command, ``next`` runs the system checks before the subcommand, so ``--skip-checks`` prints the table of a project whose checks fail.
A subcommand that sets ``skips_checks = True`` starts without them, which ``boundaries`` does, since it reports the very findings that fail the checks.

.. code-block:: bash
   :caption: Django options before the subcommand

   uv run python manage.py next --skip-checks routes

routes
~~~~~~

``manage.py next routes`` prints one row per routed page, in resolution order, with six columns.

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Column
     - Content
   * - ``URL``
     - The absolute route in Django :func:`~django.urls.path` syntax, from the root URLconf.
   * - ``Name``
     - The namespaced reverse name.
   * - ``App``
     - The label of the owning application, ``-`` for a project root.
   * - ``File``
     - The ``page.py``, or the ``template.djx`` of a virtual page, relative to ``BASE_DIR`` when it lies under it.
   * - ``Source``
     - ``root`` for a tree at the include point of ``next.urls``, ``mounts`` for a tree an ``AppConfig`` mounts through ``mount`` or ``mounts``, ``urls.py`` for a tree a URLconf mounts through ``pages()``.
   * - ``Access``
     - The access rule of the page, spelled as in `permissions`_.

.. code-block:: text
   :caption: manage.py next routes

   URL                                Name                                     App      File                        Source   Access
   /staff/                            audit:page_                              audit    audit/pages/page.py         urls.py  login, perm audit.view_entry
   /staff/log/                        audit:page_log                           audit    audit/pages/log/page.py     urls.py  login, perm audit.view_entry
   /wallets/                          next:page_wallets                        wallets  wallets/pages/page.py       mounts   login
   /wallets/<int:wallet>/operations/  next:page_wallets_int_wallet_operations  wallets  wallets/operations/page.py  mounts   login, perm wallets.view_wallet on wallets.Wallet
   /                                  next:page_                               notes    notes/pages/page.py         root     -

A tree of ``next.urls`` is listed only when the root URLconf includes ``next.urls``.
The table lists exactly the routed pages, so a page whose route the URL parser refuses and a ``page.py`` with no body source to render are left out.
Under :func:`~django.conf.urls.i18n.i18n_patterns` a URL carries the prefix of ``LANGUAGE_CODE``.
:ref:`topics-file-router-mounting` covers the three sources.

permissions
~~~~~~~~~~~

``manage.py next permissions`` prints the access rule of every page, every action, and every component whose ``component.py`` declares access, with four columns.

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Column
     - Content
   * - ``Kind``
     - ``page``, ``action``, or ``component``.
   * - ``Endpoint``
     - The URL of a page, the name of an action, or the name of a component.
   * - ``Access``
     - The requirements in the order the chain declares them, ``login`` and one ``perm <name>`` per permission with ``on <app_label.Model>`` for the row of a page or a ``proxy.py`` and ``on <name>`` for the context a component names, ``public`` for an endpoint whose rule a ``public()`` opened, in its own ``page.py`` or in a ``proxy.py`` above, ``login (middleware)`` for an endpoint without requirements whose rule is not public while ``LoginRequiredMiddleware`` is listed, ``broken`` for a chain holding a file that failed to import or, in the table of one page, for a page whose composed template does not compile, and ``-`` for an open endpoint.
   * - ``Sources``
     - The files the requirements come from, root first, relative to ``BASE_DIR`` when they lie under it, ``public (file)`` naming the file whose ``public()`` opened a public rule, or the broken file.

.. code-block:: text
   :caption: manage.py next permissions

   Kind       Endpoint                  Access                                     Sources
   page       /notes/                   login                                      notes/pages/notes/proxy.py
   page       /notes/<int:note>/edit/   login, perm notes.edit_note on notes.Note  notes/pages/notes/proxy.py, notes/pages/notes/[int:note]/edit/page.py
   page       /notes/<int:note>/share/  public                                     public (notes/pages/notes/[int:note]/share/page.py)
   page       /login/                   public                                     public (notes/pages/login/page.py)
   action     note_edit_form            login, perm notes.edit_note on notes.Note  notes/pages/notes/proxy.py, notes/pages/notes/[int:note]/edit/page.py
   component  admin_toolbar             perm notes.moderate                        notes/pages/_components/admin_toolbar/component.py

The share page lifts the login of ``notes/pages/notes/proxy.py`` through its own ``page.public()``, so its ``Sources`` column names that ``page.py``, and ``manage.py check`` lists the same lift as ``next.W135``.

``manage.py next permissions <url>`` narrows the table to the page the URL resolves to, the actions declared in its directory, and the declaring components its composed template renders, nested ones included.
A URL that resolves to no page is a ``CommandError``.
The rule of an action is its owner rule and its ``Meta`` guard, since the rule of the posted origin page joins only per request, see :doc:`/content/security/actions`.

boundaries
~~~~~~~~~~

``manage.py next boundaries`` prints the boundary debt of the project, one row per application and code, with four columns.
A ``Declarations`` section comes first while an ``AppConfig`` breaks ``next.E174`` or ``next.E175``, one line per finding with its file, line, code, and message.

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Column
     - Content
   * - ``App``
     - The label of the application, ``-`` for a record of ``boundaries.toml`` whose file lies in no application.
   * - ``Code``
     - A code ``boundaries.toml`` may record.
   * - ``Found``
     - How many findings of the code the files of the application hold.
   * - ``Recorded``
     - How many of them ``boundaries.toml`` records.

.. code-block:: text
   :caption: manage.py next boundaries

   App      Code       Found  Recorded
   library  next.W136  1      1
   Findings: 1, recorded in boundaries.toml: 1. Mode: strict.

The last line sums both columns and names the mode of ``NEXT_FRAMEWORK["BOUNDARIES"]``, and a project without findings prints ``No boundary findings.`` with the mode.
A line per shared kernel follows, an application whose dotted name ends in ``core``, with its modules, its non-empty lines, and the number of applications that use it, see :ref:`topics-boundaries-shared-kernel`.
The command starts without the system checks, so it answers while ``manage.py check --tag boundaries`` fails, and exits with status zero whatever it finds, unless ``boundaries.toml`` or the import scan is broken or the boundaries go unchecked, which stop it with a ``CommandError``.
Without ``NEXT_FRAMEWORK["BOUNDARIES"]`` every mode of the command stops with the one line ``Boundaries are off, set NEXT_FRAMEWORK['BOUNDARIES'] to 'warn' or 'strict'.``

``manage.py next boundaries --exact`` adds a section after the table with each entry of ``depends_on`` that names an application of the project no edge leads to, and then fails with a ``CommandError`` that counts them.
It prints ``Every depends_on entry is used.`` when none is left, and ``No app declares depends_on, so there is nothing to compare.`` when no application declares the attribute, and both exit with status zero.
It compares the whole project, so it takes no application, see :ref:`topics-boundaries-exact`.

``manage.py next boundaries <app>`` prints the Module Canvas of one application, named by its label or its dotted name, what it owns, publishes, and leans on, one section after another.
An isolated application gets the line ``Isolated: yes`` under its name.

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Section
     - Content
   * - ``Models``
     - Each model of the application with its relation fields that point at a model of another application.
   * - ``Publishes``
     - Each signal its ``signals.py`` declares and each public function of its ``services`` module.
   * - ``Listens``
     - Each signal or sender of another application its receivers name, with the file and the line.
   * - ``DI keys``
     - Each named dependency it provides, with the file and the line.
   * - ``Pages``
     - Its pages, actions, and declaring components, in the four columns of `permissions`_.
   * - ``Risks``
     - Each action open to anyone while nothing declares it public.
   * - ``Depends on``
     - Each application it has an edge to, with the count of every kind of edge, and ``(undeclared)`` after an application its ``depends_on`` leaves out.
   * - ``Used by``
     - Each application with an edge to it, counted the same way.

An empty section prints a dash, and a key, label or name, that names no application of the project is a ``CommandError`` that lists the labels there are.
In every mode an import graph the backend cannot build, ``next.E173``, a backend the settings name wrongly, ``next.E176``, a ``boundaries.toml`` that breaks its format, and a strict mode that goes unchecked, ``next.W148``, are a ``CommandError`` as well.

``manage.py next boundaries --refreeze`` writes ``boundaries.toml`` and prints what it wrote.
Without a file it records every finding, and with one it only shrinks the records, then prints how many findings it left unrecorded.
Under settings that leave applications of the project out it keeps every record of a file still on disk as it is, see :doc:`/content/deployment/split-deployments`.
It records the whole project, so it takes no application label.
A ``boundaries.toml`` it cannot read and a project without ``BASE_DIR`` stop it with a ``CommandError``.
:doc:`/content/topics/boundaries` explains the ratchet the file holds.

``manage.py next boundaries sync`` prints the unified diff of the names each application of the project needs in its ``depends_on``, and ``--add`` writes the same diff to the files.
``manage.py next boundaries sync <app>`` limits it to one application, named by its label or its dotted name.
It only adds names, prints ``Nothing to add.`` when every edge is declared, and builds every new text before it writes any, so a refusal leaves every file as it was.
``--add`` stages each new text in a temporary file beside its original and replaces the originals once all of them are staged, each file in one swap.
A failed swap names the file relative to ``BASE_DIR`` and counts the files already written before it, ``Files already written before it: N.``
A ``depends_on`` that is no literal tuple of strings, or an inherited one, is a ``CommandError`` that names the file and the line to edit by hand, see :ref:`topics-boundaries-sync`.
An application whose configuration class lives outside the applications of the project is skipped and counted in a closing ``Skipped apps with no AppConfig class of their own in the project`` note, and ``sync <app>`` for it is a ``CommandError`` that asks for an ``apps.py`` with an ``AppConfig`` subclass.

The second positional argument is ``sync_app`` in ``--help``, and only ``sync`` takes it.
``sync`` as the first word picks this mode, so an application labelled ``sync`` gets its Module Canvas by its dotted name, such as ``manage.py next boundaries ledger.sync``.
``--add`` and a second application go with ``sync`` alone, while ``--refreeze`` and ``--exact`` take no application at all.
Any other combination is a ``CommandError`` before the scan starts.

showmetadata
------------

``manage.py showmetadata <path>`` resolves a URL path to its page and prints, for every key the static metadata sets, the source that sets it, ``DEFAULTS`` or a ``page.py``.
Each ``@page.metadata`` callable of the page follows under ``*``, since a callable answers only per request.

.. code-block:: text
   :caption: manage.py showmetadata /posts/launch-day/

   /srv/site/blog/pages/posts/[slug]/page.py
     description: NEXT_FRAMEWORK['METADATA']['DEFAULTS']
     jsonld: NEXT_FRAMEWORK['METADATA']['DEFAULTS']
     title: /srv/site/blog/pages/page.py
     *: post_metadata in /srv/site/blog/pages/posts/[slug]/page.py

Every file is named by its absolute path.
The path resolves under the language its prefix names, ``LANGUAGE_CODE`` without one, so a translated path under :func:`~django.conf.urls.i18n.i18n_patterns` reaches its page.
A path that resolves to no URL, or to a view that is no page, is a ``CommandError``.

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the routes ``next routes`` lists.
   :doc:`/content/topics/permissions` for the rules ``next permissions`` lists.
   :doc:`/content/topics/boundaries` for the findings ``next boundaries`` counts.
   :doc:`/content/topics/seo/auditing` for where the ``showmetadata`` command fits beside the checks and the tests.
   :doc:`metadata` for the metadata the command reads.
