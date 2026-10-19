.. _ref-management:

Management commands
===================

Module summary
--------------

The ``next`` application ships three management commands, and ``manage.py help`` lists them under ``[next]``.
``routes`` prints the page routes, ``permissions`` prints the access rule of every endpoint, and ``showmetadata`` is a thin shell over the metadata of ``next.pages``.
Like any Django command, each one runs the system checks first, so ``--skip-checks`` prints the table of a project whose checks fail.

routes
------

``manage.py routes`` prints one row per routed page, in resolution order, with four columns.

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Column
     - Content
   * - ``URL``
     - The absolute route in Django :func:`~django.urls.path` syntax, from the root URLconf.
   * - ``Name``
     - The namespaced reverse name.
   * - ``File``
     - The ``page.py``, or the ``template.djx`` of a virtual page, relative to ``BASE_DIR`` when it lies under it.
   * - ``Access``
     - The access rule of the page, spelled as in `permissions`_.

.. code-block:: text
   :caption: manage.py routes

   URL                 Name                      File                                  Access
   /staff/             audit:page_               audit/pages/page.py                   login, perm audit.view_entry
   /staff/log/         audit:page_log            audit/pages/log/page.py               login, perm audit.view_entry
   /                   next:page_                notes/pages/page.py                   -
   /notes/<int:note>/  next:page_notes_int_note  notes/pages/notes/[int:note]/page.py  login

A tree of ``next.urls`` is listed only when the root URLconf includes ``next.urls``.
The table lists exactly the routed pages, so a page whose route the URL parser refuses and a ``page.py`` with no body source to render are left out.
Under :func:`~django.conf.urls.i18n.i18n_patterns` a URL carries the prefix of ``LANGUAGE_CODE``.
:ref:`topics-file-router-trees` covers where each tree routes.

permissions
-----------

``manage.py permissions`` prints the access rule of every page, every action, every component whose ``component.py`` declares access, and every other view of the URLconf, with four columns.

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Column
     - Content
   * - ``Kind``
     - ``page``, ``action``, ``component``, or ``view``.
   * - ``Endpoint``
     - The URL of a page or a view, the name of an action followed by the file declaring it in parentheses, or the name of a component.
   * - ``Access``
     - The requirements in the order the chain declares them, each once, in the words listed below.
   * - ``Sources``
     - The files the requirements come from, root first, relative to ``BASE_DIR`` when they lie under it, or the source the list below names for the value.

.. list-table::
   :header-rows: 1
   :widths: 25 45 30

   * - ``Access``
     - Meaning
     - ``Sources``
   * - ``login``
     - A ``login_required()`` of the chain, or the ``Meta`` guard of an action.
     - The declaring files.
   * - ``perm <name>``
     - A permission asked without an object.
     - The declaring files.
   * - ``perm <name> on <app_label.Model>``
     - A permission asked on the row an ``obj=DObject[Model]`` of a ``page.py`` loads.
     - The declaring files.
   * - ``perm <name> on <name>``
     - A permission asked on the prop or the context a component names.
     - The ``component.py``.
   * - ``perm <name> on form``
     - A permission ``add_perm`` registers, named in the ``Meta`` guard of an action whose ``form_class`` binds a form, and asked of the bound form.
     - The file declaring the action.
   * - ``public``
     - A rule a ``public()`` opened, in the ``page.py`` of the page or with ``inherit=True`` above it, or a view :func:`~django.contrib.auth.decorators.login_not_required` marks.
     - ``public (file)`` naming the opening ``page.py``, or ``public (module.view)`` for a view.
   * - ``login (default)``
     - A page or an action that declares nothing and is not public, while ``NEXT_FRAMEWORK["LOGIN_REQUIRED"]`` or a listed ``LoginRequiredMiddleware`` turns the default login on.
     - ``LOGIN_REQUIRED`` when the key is on, else the class name of the listed middleware or its subclass.
   * - ``login (middleware)``
     - A view outside the page trees that the listed middleware closes, which the key never closes.
     - The class name of the middleware.
   * - ``broken``
     - A chain holding a ``page.py`` that failed to import, or, in the table of one page, a page whose composed template does not compile.
     - The broken file.
   * - ``per action``
     - The action endpoint, whose rule each ``action`` row shows.
     - ``see the action rows``.
   * - ``-``
     - An open endpoint, or a view that decides access in its own code.
     - ``-``.

.. code-block:: text
   :caption: manage.py permissions

   Kind       Endpoint                                                    Access                                                                   Sources
   page       /notes/                                                     login                                                                    notes/pages/notes/page.py
   page       /notes/<int:note>/edit/                                     login, perm notes.edit_note on notes.Note                                notes/pages/notes/page.py, notes/pages/notes/[int:note]/edit/page.py
   page       /notes/<int:note>/share/                                    public                                                                   public (notes/pages/notes/[int:note]/share/page.py)
   page       /login/                                                     public                                                                   public (notes/pages/login/page.py)
   page       /                                                           login (default)                                                          LoginRequiredMiddleware
   action     note_edit_form (notes/pages/notes/[int:note]/edit/page.py)  login, perm notes.edit_note on notes.Note, perm notes.edit_note on form  notes/pages/notes/page.py, notes/pages/notes/[int:note]/edit/page.py
   component  admin_toolbar                                               perm notes.moderate                                                      notes/pages/_components/admin_toolbar/component.py
   view       /sw.js                                                      public                                                                   public (config.pwa.worker)
   view       /exports/                                                   login (middleware)                                                       LoginRequiredMiddleware
   view       /_next/form/<str:uid>/                                      per action                                                               see the action rows

The share page lifts the login ``notes/pages/notes/page.py`` passes down through its own ``page.public()``, so its ``Sources`` column names that ``page.py``.
The action asks ``notes.edit_note`` twice, on the note the URL names by the rule of its page and on the bound form by its ``Meta`` guard, which is the order the dispatcher checks them in.
An action carries the path of its file, the same path the ``File`` column of ``manage.py routes`` shows for the page declaring it, since two pages may declare actions of one name.

The ``view`` rows walk the patterns of ``django.urls.get_resolver()`` through every include in URLconf order and leave out the patterns of the page trees, which already have ``page`` rows.
They show without the middleware too, as ``-`` for a view that decides access in its own code, a view behind Django's :func:`~django.contrib.auth.decorators.login_required` among them, so the table lists everything the project answers.
The ``/_next/form/<str:uid>/`` endpoint is open to the middleware, yet it reads ``per action``, since the dispatcher applies the rule of each action its ``action`` row shows.
The dispatch view carries a ``next_access_kind`` attribute naming the kind of rows that decide it, which is how the table tells it from a public view.

``manage.py permissions <url>`` narrows the table to the page the URL resolves to, the actions declared in its directory, and the declaring components its composed template renders, nested ones included.
A URL that resolves to a view outside the page trees prints the row of that view alone, and a URL that resolves to nothing is a ``CommandError``.
The rule of an action is its owner rule and its ``Meta`` guard, the same rule the dispatcher enforces, since the rule of the posted origin page joins only per request, see :doc:`/content/security/actions`.

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

   :doc:`/content/topics/file-router` for the routes ``manage.py routes`` lists.
   :doc:`/content/topics/permissions` for the rules ``manage.py permissions`` lists.
   :doc:`/content/topics/seo/auditing` for where the ``showmetadata`` command fits beside the checks and the tests.
   :doc:`metadata` for the metadata the command reads.
