.. _topics-project-layout:

Project layout
==============

A next.dj project is a Django project, and its unit of code is the Django application.
Each application owns the models, migrations, pages, and components of one subject area, and its files carry fixed names, so every application opens the same way.
This page names those files and their roles, shows the Notes project from the tutorial in that shape, and covers how a project grows from one application to many.
:doc:`application-layout` decides which of those files a piece of logic belongs in, and :doc:`multi-project` covers several Django projects in one repository.
:doc:`boundaries` checks on every ``manage.py check`` that the applications keep to these roles.

.. contents::
   :local:
   :depth: 2

Application composition
-----------------------

Domain code sits flat at the root of the application under fixed names, and the URL tree grows only inside ``pages/``.
A page at any depth reaches the domain through the same absolute import, such as ``from notes.services import archive_note``, because the domain code never moves when the URL tree does.
The ``startapp`` template of next.dj writes this shape for a new application, see :ref:`intro-install-project-template`.

.. code-block:: text
   :caption: one application

   notes/
     __init__.py
     apps.py
     models.py
     services.py
     forms.py
     signals.py
     deps.py
     permissions.py
     admin.py
     migrations/
     tests/
     pages/
       layout.djx
       proxy.py
       page.py
       template.djx
       _components/
         note_card/
           component.djx
           component.py
           component.css
       notes/
         _components/
           note_toolbar/
             component.djx
         [int:id]/
           page.py
           template.djx

Every file has one role.
The last column names what another application of the same project may use, and the boundary checks hold the project to it, see :ref:`topics-boundaries-public-modules`.

.. list-table::
   :header-rows: 1
   :widths: 16 32 28 24

   * - File
     - Holds
     - Keeps out
     - Open to other applications
   * - ``apps.py``
     - The ``AppConfig`` with ``name``, an explicit ``label``, and an optional ``mount`` or ``mounts`` prefix.
     - Domain code, and model imports at module level, which run before the app registry is ready.
     - No.
   * - ``models.py``
     - Fields, methods that act on one instance, a custom ``QuerySet`` for reads over many rows with ``visible_to(scope)`` on the default manager, and invariants in ``Meta.constraints`` and ``clean``.
     - Requests, responses, templates, and writes that span several models.
     - Yes.
       Foreign keys and ``QuerySet`` methods are how Django applications meet.
   * - ``services.py``
     - Functions shaped ``f(scope, *, ...)`` that span several models, open transactions, and send signals, plus public reads that take no scope.
     - ``request``, ``HttpResponse``, and rendering.
     - Yes, it is the write surface of the application.
   * - ``forms.py``
     - Form classes with their validation and the ``Meta`` guards ``login_required`` and ``permission_required``.
     - Domain rules, which ``on_valid`` reaches through ``services.py``.
     - No.
   * - ``signals.py``
     - The signals the application declares and the receivers it connects, including receivers on the signals of other applications.
     - Logic beyond a call into a ``services.py``.
     - Yes, the signals it declares.
   * - ``deps.py``
     - DI providers and named dependencies that turn the request into a value, such as the ``scope`` every read filters by or a feature flag.
     - Domain rules and writes.
     - Named dependencies are project-wide through ``Depends("name")``, while the module itself stays private.
   * - ``permissions.py``
     - The object permissions of the application, a predicate taking ``()``, ``(user)``, or ``(user, obj)`` registered per ``app_label.codename`` through ``add_perm``.
     - The request, the tenant, and writes, which a form hook or a service reads.
     - Yes, through ``user.has_perm()``, which answers a permission of any application.
   * - ``admin.py``
     - ``ModelAdmin`` registrations whose ``save_model`` and admin actions call ``services.py``.
     - Writes that bypass ``services.py``.
     - No.
   * - ``migrations/``
     - The migrations ``makemigrations`` writes.
     - Nothing next.dj adds.
     - No.
   * - ``tests/``
     - The tests of this application.
     - Tests of other applications.
     - No.
   * - ``pages/``
     - Delivery, which means ``page.py`` with its context, actions, access, and metadata, ``layout.djx`` with the markup of a section, ``proxy.py`` with the access of a section, ``template.djx``, and co-located assets.
     - Domain rules, model writes outside a form or a service, and every other Python module.
     - No, a page tree has no import path.
   * - ``pages/_components/``
     - The components of this application, each ``component.py`` beside its template and assets, at the tree root or nested under a subtree.
     - Domain rules, as for pages.
     - No.
       A page-tree component is visible only from templates of its own tree, and a kit shared across applications lives in a ``DIRS`` root.

:doc:`application-layout` shows the ``scope`` dependency, the ``visible_to(scope)`` read, and the ``f(scope, *, ...)`` service in full.

Five of these files load with no import of their own, so ``AppConfig.ready`` stays empty.
When the ``next`` application is ready it imports ``deps.py``, then ``permissions.py``, then ``forms.py``, then ``signals.py`` of every installed application, all before any ``component.py``, and ``django.contrib.admin`` imports ``admin.py``.
``DEPS_AUTODISCOVER``, ``PERMISSIONS_AUTODISCOVER``, ``FORM_AUTODISCOVER``, and ``SIGNALS_AUTODISCOVER`` turn the four sweeps off one by one, and a project that turns one off imports those modules from ``AppConfig.ready``, see :doc:`/content/ref/settings`.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig

   class NotesConfig(AppConfig):
       default_auto_field = "django.db.models.BigAutoField"
       name = "notes"
       label = "notes"

Names the layout avoids
~~~~~~~~~~~~~~~~~~~~~~~

``providers.py``, a ``backends.py`` that holds DI providers, ``receivers.py``, ``policy.py``, ``access.py``, ``facades.py``, ``selectors.py``, and ``queries.py`` stay out of an application.
The reason is the same for all of them, one obvious place per role.
Reads live on the ``QuerySet``, DI in ``deps.py``, signals and their receivers in ``signals.py``, and the operations other applications call in ``services.py``.
A ``backends.py`` that subclasses a real next.dj backend, such as ``StaticFilesBackend`` or ``FileRouterBackend``, keeps its name.
``next.W147`` reports the other names at the root of an application, see :ref:`topics-boundaries-file-names`.

Delivery stays in pages
-----------------------

The page tree holds delivery files and nothing else.
These are ``page.py``, ``proxy.py``, ``layout.djx``, ``template.djx``, ``component.py``, ``component.djx``, their co-located assets, and the ``sitemap.py``, ``robots.py``, and ``scripts.py`` sources the framework reads at the top of a page root.

The reason is how the tree loads.
The loader executes each ``page.py`` by its file path under one fixed module name, outside any package, and treats ``component.py`` the same way.
A relative import in such a module has no parent package to resolve against, and a directory such as ``[int:id]`` is no Python identifier, so a helper module placed beside a page has no import path that holds at every depth.
Domain code therefore lives at the application root, and the page reaches it through an absolute import.

A ``page.py`` reads through model and ``QuerySet`` methods and writes through a form or a function of ``services.py``.
:doc:`application-layout` shows both sides of that call, and :ref:`topics-boundaries-visible-to` and :ref:`topics-boundaries-writes` describe the checks that report a read or a write past them.

The Notes project
-----------------

The tree below is the Notes project as the tutorial leaves it, and it already follows the composition above.
The tutorial creates ``models.py``, ``forms.py``, the page tree, and the tests.
``services.py`` and ``signals.py`` join once the application grows a write that spans several models or a reaction to another application, and ``deps.py`` joins once a page needs a value derived from the request.

.. code-block:: text
   :caption: full project tree

   notes_project/
     manage.py
     pyproject.toml
     pytest.ini
     config/
       __init__.py
       settings.py
       urls.py
       wsgi.py
       asgi.py
     notes/
       __init__.py
       apps.py
       models.py
       forms.py
       admin.py
       migrations/
       templates/
         registration/
           login.html
       pages/
         layout.djx
         page.py
         template.djx
         _components/
           note_card/
             component.djx
             component.py
             component.css
             component.js
         about/
           page.py
           template.djx
         notes/
           layout.djx
           [int:id]/
             page.py
             template.djx
             edit/
               page.py
               template.djx
     tests/
       test_notes_actions.py
       test_notes_e2e.py
       test_notes_partial.py
       test_notes_signals.py

Two things are special about this tree.

- ``pages/`` is the page root.
  Every directory below it becomes a URL, except a private one whose name starts with an underscore.
- ``_components/`` lives inside the page root.
  Every directory below it becomes a reusable component.

The file router registers each ``_components/`` folder it meets while walking the page tree, and it skips that folder as a route.
A ``_components/`` folder placed beside ``pages/`` rather than inside it is never walked, so the ``{% component %}`` tag finds nothing there.
A component root outside the page tree needs an explicit ``DIRS`` entry on the component backend, covered in :doc:`/content/howto/share-components-across-projects`.

Configuration touchpoints
-------------------------

Two ``NEXT_FRAMEWORK`` keys point at the directories above.
A project ``static/`` directory is reached through Django ``STATICFILES_DIRS`` instead, see *Static files* below.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [],
               "APP_DIRS": True,
               "PAGES_DIR": "pages",
               "OPTIONS": {"context_processors": []},
           }
       ],
       "COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [],
               "COMPONENTS_DIR": "_components",
           }
       ],
   }

``PAGES_DIR`` names the page root inside each application.
``COMPONENTS_DIR`` names the component folder the router looks for inside that page root.
Both names are configurable for a tree that already exists under other names.
This page, the tutorial, and every repository example use ``pages`` and ``_components``.

Settings helpers
----------------

When you need to override a single key inside ``PAGE_BACKENDS`` without rewriting the entire list, use ``extend_default_backend``.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": extend_default_backend(
           "PAGE_BACKENDS",
           OPTIONS={"context_processors": ["myapp.context.site"]},
       )
   }

The helper deep copies the default list and patches the first backend entry.
Pass ``index=`` to patch a different entry when the default list carries more than one backend.
Scalar and list overrides replace the existing value.
Dict overrides such as ``OPTIONS`` are merged one level deep into the default dict.

The default backend ships ``OPTIONS`` carrying only the ``context_processors`` key.
The override merges into that dict one level deep, so any key the override omits keeps its default value while the supplied ``context_processors`` list replaces the empty default.
:doc:`/content/howto/extend-a-default-backend` covers the helper in full.

Per project page DIRS
---------------------

A project that hosts a global layout or a project-wide page tree adds an entry to ``DIRS``.

.. code-block:: python
   :caption: config/settings.py

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [str(BASE_DIR / "chrome")],
               "APP_DIRS": True,
               "PAGES_DIR": "pages",
               "OPTIONS": {"context_processors": []},
           }
       ]
   }

The ``chrome`` directory holds a top level ``layout.djx`` plus optional project pages.
The router walks the application directories first, then enters ``chrome``.
The chrome layout wraps every application page.

Project sizes
-------------

The composition of an application stays the same at every size.
What changes is how many applications a project holds and how they are grouped.

One application
~~~~~~~~~~~~~~~

A product with one subject area lives in one application, and that is a complete shape rather than a step towards a split.
When ``models.py`` or ``services.py`` outgrows one screen it becomes a package of the same name with one-word submodules.
The package ``__init__.py`` imports its submodules, which is how Django finds models in a ``models/`` package, so ``from notes.services import archive_note`` keeps working.

.. code-block:: text
   :caption: one application with packages

   notes/
     apps.py
     models/
       __init__.py
       note.py
       tag.py
     services/
       __init__.py
       notes.py
       sharing.py
     forms.py
     signals.py
     migrations/
     pages/

A split into several applications moves models between migration histories, which is hard to undo.
Split once the boundaries of the domain are known rather than up front.

Several applications
~~~~~~~~~~~~~~~~~~~~

Several subject areas become several applications in ``INSTALLED_APPS``, each with the composition above.
With ``APP_DIRS`` on, the file router walks the ``pages/`` tree of every installed application and serves the routes of all of them through the single ``include("next.urls")`` line.
By default the first directory inside ``pages/`` is the first URL segment, for example ``billing/pages/billing/invoices/page.py`` for ``/billing/invoices/``.
An application that owns a prefix declares it on its ``AppConfig`` instead, and its tree loses the repeated directory.

.. code-block:: python
   :caption: billing/apps.py

   from django.apps import AppConfig

   class BillingConfig(AppConfig):
       default_auto_field = "django.db.models.BigAutoField"
       name = "billing"
       label = "billing"
       mount = "billing/"

With that declaration ``billing/pages/invoices/page.py`` answers ``/billing/invoices/``, and its URL name ``next:page_billing_invoices`` stays the one the deeper tree had.
An application with a second tree, such as a staff area beside its public pages, maps each subdirectory to its prefix through ``mounts``, as :ref:`topics-file-router-mounting` describes.
Two applications that answer the same URL path are reported by the ``next.E015`` check.

A layout in a project ``DIRS`` root such as ``chrome/`` wraps the pages of every application, as *Per project page DIRS* above shows.

Contexts
~~~~~~~~

A long flat list of applications groups into contexts.
A context is a plain Python package that holds applications and no code of its own, and next.dj has no notion of it.

.. code-block:: text
   :caption: applications grouped into contexts

   ledger/
     __init__.py
     identity/
       __init__.py
       accounts/
       sessions/
     money/
       __init__.py
       wallets/
         __init__.py
         apps.py
         models.py
         services.py
         pages/
       operations/

An application inside a context is an ordinary Django application whose ``name`` is its full dotted path.
Its ``label`` is set explicitly, because the default label is the last component of the name and two contexts may each hold an application of the same short name.

.. code-block:: python
   :caption: ledger/money/wallets/apps.py

   from django.apps import AppConfig

   class WalletsConfig(AppConfig):
       default_auto_field = "django.db.models.BigAutoField"
       name = "ledger.money.wallets"
       label = "wallets"

``INSTALLED_APPS`` lists ``"ledger.money.wallets"``.
The file router finds the page tree of a nested application through ``AppConfig.path``, so ``ledger/money/wallets/pages/`` is walked like any other tree and its routes join those of every other application under the same ``include("next.urls")``.

Each application of a context declares its own prefix through ``mount`` or ``mounts``, such as ``mount = "money/wallets/"`` on ``WalletsConfig``.
A context that wants to own its URL space instead keeps a ``urls.py`` of its own and mounts the trees of its applications there through ``pages()``.

.. code-block:: python
   :caption: ledger/money/urls.py

   from django.urls import path

   from next.urls import pages

   urlpatterns = [
       path("wallets/", pages("wallets")),
       path("operations/", pages("operations")),
   ]

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   urlpatterns = [
       path("money/", include("ledger.money.urls")),
       path("", include("next.urls")),
   ]

The router finds these ``pages()`` calls through the nested include, takes the two trees out of ``include("next.urls")``, and names their pages in the ``wallets`` and ``operations`` namespaces, so ``page_reverse("", namespace="wallets")`` answers ``/money/wallets/``.
A ``pages()`` include wins over a ``mount`` or ``mounts`` declaration of the same tree, and ``manage.py next routes`` lists where each tree ended up.

Tests
-----

Place tests under ``tests/`` at the project root.
``pytest.ini`` holds the collection settings and loads ``next.testing.plugin``, which imports the page tree once per session and supplies the ``next_client`` fixture, so the project needs no ``conftest.py`` of its own (see :doc:`/content/topics/testing`).
A suite with wiring beyond the plugin options adds a ``conftest.py`` for that wiring alone.

A per application ``tests/`` directory, as in the composition above, works for projects with several applications.
See :doc:`multi-project` for the layered layout.

Static files
------------

Project-wide assets that are not owned by a page or a component live under ``static/`` at the project root.
The static finders include co-located assets, the project ``static/`` directory, and every entry in ``STATICFILES_DIRS``.
A favicon such as ``static/site/icon.svg`` belongs there, and every page names it through the ``icons`` metadata key, see :doc:`/content/topics/seo/icons-and-images`.

Migrations
----------

next.dj does not touch migrations.
Run ``uv run python manage.py makemigrations`` and ``uv run python manage.py migrate`` exactly as in a regular Django project.

Custom management commands
--------------------------

Place commands inside ``notes/management/commands/``.
The framework does not require any special wiring beyond what Django already documents.

Common variations
-----------------

Project layout with chrome
~~~~~~~~~~~~~~~~~~~~~~~~~~

Add a ``chrome/`` directory at the project root and reference it through ``DIRS``.
The chrome holds a project-wide layout and possibly a few project-level pages such as ``/login`` or ``/health``.

Per domain trees
~~~~~~~~~~~~~~~~

Define two backends in ``PAGE_BACKENDS``.
Each backend walks a different directory.
The first matching URL pattern wins, so the order matters.

See also
--------

.. seealso::

   :doc:`application-layout` for where models, querysets, services, and signals hold the logic.
   :doc:`boundaries` for the checks that hold applications to their roles.
   :doc:`file-router` for the route shapes and the mounting of application trees.
   :doc:`/content/howto/mount-an-application-under-a-prefix` for a prefix recipe.
   :doc:`multi-project` for layouts that span several Django projects.
   :doc:`/content/howto/share-components-across-projects` for shared component patterns.
