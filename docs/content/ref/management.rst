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
     - The requirements in the order the chain declares them, ``login`` and one ``perm <name>`` per permission with ``on <app_label.Model>`` for the row of a page or a ``proxy.py`` and ``on <name>`` for the context a component names, ``public`` for an endpoint whose rule a ``public()`` opened, in its own ``page.py`` or in a ``proxy.py`` above, ``login (middleware)`` for an endpoint without requirements whose rule is not public while ``LoginRequiredMiddleware`` is listed, ``broken`` for a chain holding a file that failed to import or, in the table of one page, for a page whose composed template does not compile, and ``-`` for an open endpoint.
       A view reads ``public`` when :func:`~django.contrib.auth.decorators.login_not_required` marks it, ``login (middleware)`` when the middleware is listed, and ``-`` otherwise, and the action endpoint reads ``per action``.
   * - ``Sources``
     - The files the requirements come from, root first, relative to ``BASE_DIR`` when they lie under it, ``public (file)`` naming the file whose ``public()`` opened a public rule, the broken file, the class name of the listed ``LoginRequiredMiddleware`` or its subclass for a ``login (middleware)`` row, or ``-`` when there is none.
       A public view names its dotted path as ``public (module.view)``, and the action endpoint points at the ``action`` rows with ``see the action rows``.

.. code-block:: text
   :caption: manage.py permissions

   Kind       Endpoint                                                    Access                                     Sources
   page       /notes/                                                     login                                      notes/pages/notes/proxy.py
   page       /notes/<int:note>/edit/                                     login, perm notes.edit_note on notes.Note  notes/pages/notes/proxy.py, notes/pages/notes/[int:note]/edit/page.py
   page       /notes/<int:note>/share/                                    public                                     public (notes/pages/notes/[int:note]/share/page.py)
   page       /login/                                                     public                                     public (notes/pages/login/page.py)
   page       /                                                           login (middleware)                         LoginRequiredMiddleware
   action     note_edit_form (notes/pages/notes/[int:note]/edit/page.py)  login, perm notes.edit_note on notes.Note  notes/pages/notes/proxy.py, notes/pages/notes/[int:note]/edit/page.py
   component  admin_toolbar                                               perm notes.moderate                        notes/pages/_components/admin_toolbar/component.py
   view       /sw.js                                                      public                                     public (config.pwa.worker)
   view       /exports/                                                   login (middleware)                         LoginRequiredMiddleware
   view       /_next/form/<str:uid>/                                      per action                                 see the action rows

The share page lifts the login of ``notes/pages/notes/proxy.py`` through its own ``page.public()``, so its ``Sources`` column names that ``page.py``.
An action carries the path of its file, the same path the ``File`` column of ``manage.py routes`` shows for the page declaring it, since two pages may declare actions of one name.

The ``view`` rows walk the patterns of ``django.urls.get_resolver()`` through every include in URLconf order and leave out the patterns of the page trees, which already have ``page`` rows.
They show without the middleware too, as ``-`` for a view that decides access in its own code, so the table lists everything the project answers.
The ``/_next/form/<str:uid>/`` endpoint is open to the middleware, yet it reads ``per action``, since the dispatcher applies the rule of each action its ``action`` row shows.
The dispatch view carries a ``next_access_kind`` attribute naming the kind of rows that decide it, which is how the table tells it from a public view.

``manage.py permissions <url>`` narrows the table to the page the URL resolves to, the actions declared in its directory, and the declaring components its composed template renders, nested ones included.
A URL that resolves to a view outside the page trees prints the row of that view alone, and a URL that resolves to nothing is a ``CommandError``.
The rule of an action is its owner rule and its ``Meta`` guard, since the rule of the posted origin page joins only per request, see :doc:`/content/security/actions`.

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
