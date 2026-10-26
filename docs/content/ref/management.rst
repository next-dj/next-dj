.. _ref-management:

Management commands
===================

Module summary
--------------

The ``next`` application ships five management commands, and ``manage.py help`` lists them under ``[next]``.
``routes`` prints the page routes, ``permissions`` prints the access rule of every endpoint, and ``showmetadata`` is a thin shell over the metadata of ``next.pages``.
``realtime`` publishes to realtime topics and reads what the broker holds, and ``runserver`` serves the project on uvicorn with the realtime WebSocket once uvicorn is installed.
Every command runs the system checks first like any Django command, so ``--skip-checks`` prints the table of a project whose checks fail.

routes
------

``manage.py routes`` prints one row per routed page, in resolution order, with four columns, and a fifth when an intercepting page is listed.

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
   * - ``Intercept``
     - The zones an intercepting page serves, joined by ``, `` in declaration order, or ``-`` when it serves none.
       A zone the target declares or a layout above the intercept declares is no zone the intercept serves.
       A ``page.py`` with no body that only carries rules to the intercepts below it is no row.
       The column appears only when the table lists at least one intercepting page, and a routed row leaves it blank.

.. code-block:: text
   :caption: manage.py routes

   URL                 Name                      File                                  Access
   /staff/             audit:page_               audit/pages/page.py                   login, perm audit.view_entry
   /staff/log/         audit:page_log            audit/pages/log/page.py               login, perm audit.view_entry
   /                   next:page_                notes/pages/page.py                   -
   /notes/<int:note>/  next:page_notes_int_note  notes/pages/notes/[int:note]/page.py  login

An intercepting page is listed right after the row of the page it sits over, in file order when several do.
Its row repeats the ``URL`` of that page, reads ``-`` under ``Name`` because the page has no route of its own, names its own ``page.py`` or ``template.djx`` under ``File``, and shows the access rule of its own chain.
A page inside a route group shows the group in its ``File`` path, while its ``URL`` and ``Name`` leave the group out.

A tree of ``next.urls`` is listed only when the root URLconf includes ``next.urls``.
Apart from intercepting pages, the table lists exactly the routed pages, so a page whose route the URL parser refuses and a ``page.py`` with no body source to render are left out.
Under :func:`~django.conf.urls.i18n.i18n_patterns` a URL carries the prefix of ``LANGUAGE_CODE``.
:ref:`topics-file-router-trees` covers where each tree routes.

permissions
-----------

``manage.py permissions`` prints the access rule of every page, every intercepting page, every action, every component whose ``component.py`` declares access, and every other view of the URLconf, with four columns.

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Column
     - Content
   * - ``Kind``
     - ``page``, ``intercept``, ``action``, ``component``, or ``view``.
   * - ``Endpoint``
     - The URL of a page or a view, the URL of the target of an intercepting page followed by the zones it serves in parentheses, the name of an action followed by the file declaring it in parentheses, or the name of a component.
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
     - A rule a ``public()`` opened, in the ``page.py`` of the page or with ``inherit=True`` above it, or a view of the framework open to everyone, the CSRF token endpoint and the robots and sitemap views.
     - ``public (file)`` naming the opening ``page.py``, or ``public (module.view)`` for a view.
   * - ``no login``
     - A view outside the page trees that :func:`~django.contrib.auth.decorators.login_not_required` marks, so the login middleware lets it through.
       The framework knows only that the view asks no login, and the view may still refuse by its own code, through a ``permission_required`` or a secret it checks.
     - ``login_not_required (module.view)``.
   * - ``login (default)``
     - A page or an action that declares nothing and is not public, while ``NEXT_FRAMEWORK["LOGIN_REQUIRED"]`` or a listed ``LoginRequiredMiddleware`` turns the default login on.
     - ``LOGIN_REQUIRED`` when the key is on, else the class name of the listed middleware or its subclass.
   * - ``login (middleware)``
     - A view outside the page trees that the listed middleware closes, which the key never closes.
       A login is the least such a view asks, since what it checks on its own, such as the staff flag of the Django admin or a ``permission_required``, leaves no mark.
     - The class name of the middleware.
   * - ``broken``
     - A chain holding a ``page.py`` that failed to import, or, in the table of one page, a page whose composed template does not compile.
     - The broken file.
   * - ``per action``
     - The action endpoint, whose rule each ``action`` row shows.
     - ``see the action rows``.
   * - ``-``
     - A page or an action whose chain declares nothing while the default login is off, so the framework asks nothing of it.
     - ``-``.
   * - ``?``
     - A view outside the page trees that no mark and no listed middleware decide, such as one behind Django's :func:`~django.contrib.auth.decorators.login_required` or :func:`~django.contrib.auth.decorators.permission_required`, a ``LoginRequiredMixin`` view, or a view checking access in its own ``dispatch``.
       Such a decorator leaves no mark on the view, so the framework cannot read its rule.
     - The dotted path of the view.

.. code-block:: text
   :caption: manage.py permissions

   Kind       Endpoint                                                    Access                                                                   Sources
   page       /notes/                                                     login                                                                    notes/pages/notes/page.py
   page       /notes/<int:note>/edit/                                     login, perm notes.edit_note on notes.Note                                notes/pages/notes/page.py, notes/pages/notes/[int:note]/edit/page.py
   page       /notes/<int:note>/share/                                    public                                                                   public (notes/pages/notes/[int:note]/share/page.py)
   page       /login/                                                     public                                                                   public (notes/pages/login/page.py)
   page       /                                                           login (default)                                                          LOGIN_REQUIRED
   action     note_edit_form (notes/pages/notes/[int:note]/edit/page.py)  login, perm notes.edit_note on notes.Note, perm notes.edit_note on form  notes/pages/notes/page.py, notes/pages/notes/[int:note]/edit/page.py
   component  admin_toolbar                                               perm notes.moderate                                                      notes/pages/_components/admin_toolbar/component.py
   view       /sw.js                                                      no login                                                                 login_not_required (config.pwa.worker)
   view       /_next/csrf/                                                public                                                                   public (next.csrf.csrf_view)
   view       /exports/                                                   ?                                                                        config.views.exports
   view       /_next/form/<str:uid>/                                      per action                                                               see the action rows

The share page lifts the login ``notes/pages/notes/page.py`` passes down through its own ``page.public()``, so its ``Sources`` column names that ``page.py``.
The action asks ``notes.edit_note`` twice, on the note the URL names by the rule of its page and on the bound form by its ``Meta`` guard, which is the order the dispatcher checks them in.
An action carries the path of its file, the same path the ``File`` column of ``manage.py routes`` shows for the page declaring it, since two pages may declare actions of one name.

An ``intercept`` row follows the ``page`` row of its target.
Its ``Access`` and ``Sources`` come from the chain of the intercepting page, which climbs the file-system ancestors of that page and not those of the target.
Both chains run on a request, and the stricter one wins.

The ``view`` rows walk the patterns of ``django.urls.get_resolver()`` through every include in URLconf order and leave out the patterns of the page trees, which already have ``page`` rows.
They show without the middleware too, as ``?`` for a view that decides access in its own code, a view behind Django's :func:`~django.contrib.auth.decorators.login_required` among them, so the table lists everything the project answers.
``NEXT_FRAMEWORK["LOGIN_REQUIRED"]`` closes the pages and the actions alone, so ``/exports/`` above reads ``?`` under the key, and a listed ``LoginRequiredMiddleware`` would make it ``login (middleware)``.
``/sw.js`` reads ``no login`` rather than ``public``, since ``login_not_required`` tells only that the middleware lets the view through, while ``/_next/csrf/`` is a view of the framework that answers anyone.
The ``/_next/form/<str:uid>/`` endpoint is open to the middleware, yet it reads ``per action``, since the dispatcher applies the rule of each action its ``action`` row shows.
The dispatch view carries a ``next_access_kind`` attribute naming the kind of rows that decide it, which is how the table tells it from a view that asks no login.

``manage.py permissions <url>`` narrows the table to the page the URL resolves to, the intercepting pages over it, the actions declared in its directory, and the declaring components its composed template renders, nested ones included.
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

realtime
--------

``manage.py realtime`` takes one action, and every action prints text by default and one JSON document with ``--json``.
A refused topic, payload, cursor or broker raises ``ConsoleError`` from ``next.realtime.errors``, which the command turns into a ``CommandError``, so it exits with status 1 and prints the reason.

.. code-block:: text

   manage.py realtime publish <topic> (--refresh <zone> [<zone> ...] | --event <name> [--data <json>])
   manage.py realtime positions <topic> [<topic> ...]
   manage.py realtime presence <topic>
   manage.py realtime replay <topic> --after <epoch>:<seq>

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Action
     - What it does
   * - ``publish``
     - Refuses a ``MemoryBroker``, whose topics no running server can see, and otherwise publishes the zone refreshes, or one event whose detail ``--data`` gives as a JSON object, through ``next.realtime.publish`` without waiting for a transaction, and prints the topic and the ``<epoch>:<seq>`` position the broker assigned.
       ``--refresh`` and ``--event`` exclude each other, ``--data`` needs ``--event``, and a reserved event name or a payload ``publish()`` refuses is an error.
       The JSON form is the published message as the broker stores it.
   * - ``positions``
     - Prints the latest ``<epoch>:<seq>`` of each topic in one batched read, ``-`` for a topic never published.
       The JSON form maps each topic to its ``epoch`` and ``seq``, or to ``null``.
   * - ``presence``
     - Prints the attendee count of the topic and, one JSON object per line, the members the snapshot lists.
       A broker that is no ``PresenceBroker`` is an error.
       The JSON form carries ``topic``, ``count`` and ``members``.
   * - ``replay``
     - Prints the logged messages after the cursor, each with its position, its publication time in UTC and its patches, or says that nothing follows the cursor or that the log cannot close the gap, which is what makes a subscriber resync.
       The cursor is the form ``positions`` prints.
       A broker that is no ``ReplayingBroker`` is an error.
       The JSON form carries ``topic``, ``after``, ``complete`` and ``messages``.

.. code-block:: text
   :caption: manage.py realtime positions poll:1 poll:2

   Topic   Position
   poll:1  3f9c2a71b04d8e65:42
   poll:2  -

A topic name follows the rules of :doc:`realtime`, so a malformed name or one under ``next:`` is an error before the broker is reached, and a broker that cannot be reached is an error naming the cause.
``MemoryBroker`` keeps its topics inside one process, so ``publish`` under it is an error that points to a shared broker such as ``RedisBroker``, and the three reads warn on stderr that they read no server's state.
Code that runs inside the server process calls ``next.realtime.publish`` directly.

runserver
---------

``manage.py runserver`` takes the arguments of Django's :doc:`runserver <django:ref/django-admin>`, ``addrport``, ``--ipv6``, ``--noreload``, ``--nothreading`` and ``--skip-checks``, and with ``django.contrib.staticfiles`` installed its ``--nostatic`` and ``--insecure``.
``--wsgi`` serves Django's WSGI handler even with an ASGI server installed.
The command class extends the staticfiles ``runserver``, so a project command that subclasses ``next.management.commands.runserver.Command`` keeps the static options and the static serving too.

With uvicorn installed it runs the system checks and the migration check as Django does, then serves the project over ASGI on the server ``NEXT_FRAMEWORK['DEV_SERVER']`` names, uvicorn by default.
The checks run before the command resolves the server, so a ``DEV_SERVER`` that names no server stops it with ``next.E212``, and under ``--skip-checks`` with a ``CommandError`` that names the path and ``next.E212``.
The banner reads ``Starting ASGI development server with the realtime WebSocket`` only while the app mounts the socket and uvicorn finds a WebSocket library, and Django's development-server warning follows it unless ``DJANGO_RUNSERVER_HIDE_WARNING`` is ``true``.
A numeric ``addrport`` host shows as the socket reads it, so ``0`` binds every interface and shows as ``0.0.0.0``.
The app is the one the ``ASGI_APPLICATION`` setting names by dotted path, the key Daphne and Channels read, since Django has no ASGI setting of its own.
Without the setting it is ``next.realtime.application`` over :func:`~django.core.asgi.get_asgi_application`, which serves the realtime WebSocket at ``REALTIME['WS_PATH']`` and turns ``ws`` on for the process.
Under ``DEBUG``, or with ``--insecure``, static files under ``STATIC_URL`` come from the staticfiles finders through Django's ``ASGIStaticFilesHandler`` unless ``--nostatic`` is given, and a WebSocket passes through it.
Django's autoreloader restarts the process, so the watch list of ``next.server`` applies as it does under WSGI and uvicorn runs without a reloader of its own, while ``--nothreading`` has no effect.
A uvicorn that finds neither ``websockets`` nor ``wsproto`` cannot accept a WebSocket, so the command warns on stderr while the app mounts the socket, and the pages fall back to SSE.
The command hands uvicorn ``ws_max_size`` set to the 64 KiB inbound frame limit and ``ws_ping_interval`` set to ``None``, because the socket pings after one ``HEARTBEAT`` with nothing to send or no frame from the client, however busy the topic, and drops a peer that sent nothing for more than two heartbeats after an unanswered ping, so a server-level ping is only an extra layer.
It leaves lifespan on uvicorn's ``auto``, since ``next.realtime.application`` answers lifespan itself and an app from ``ASGI_APPLICATION`` may not speak it.

Without uvicorn, or with ``--wsgi``, the command is Django's ``runserver`` with the static serving of ``django.contrib.staticfiles``.

Django runs the command of the application listed first in ``INSTALLED_APPS``, so ``next`` sits above ``django.contrib.staticfiles``, as Daphne asks of its own ``runserver``.
Listed below it, ``manage.py runserver`` is the staticfiles command and serves WSGI, where the pages speak SSE, and ``next.W148`` reports the order while an ASGI server is installed.
A framework CLI that serves the same app calls ``serve_development`` from ``next.server.asgi``, see :doc:`server`.

.. code-block:: python
   :caption: settings.py

   INSTALLED_APPS = [
       "django.contrib.auth",
       "django.contrib.contenttypes",
       "django.contrib.sessions",
       "next",
       "django.contrib.staticfiles",
   ]

Use your own development server
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``NEXT_FRAMEWORK['DEV_SERVER']`` names the class the command serves on, see :doc:`settings`.
The class is built without arguments, and ``run`` receives the app, ``host``, ``port``, and the ``ws_max_size`` and ``ws_ping_interval`` options of ``server_options()``, which it maps onto its own server or ignores.
Hypercorn stands in here as one example, and the framework depends on neither it nor uvicorn.

.. code-block:: python
   :caption: config/devserver.py

   import asyncio

   from hypercorn.asyncio import serve
   from hypercorn.config import Config

   class HypercornServer:
       def run(self, app, *, host, port, **options):
           config = Config()
           config.bind = [f"{host}:{port}"]
           config.websocket_max_message_size = options.get("ws_max_size", 65536)
           asyncio.run(serve(app, config))

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {"DEV_SERVER": "config.devserver.HypercornServer"}

The rest of the command stays the same, from the system checks and Django's autoreloader to the static files and the banner.
A path that does not import or names no class with a ``run`` method is ``next.E212`` at ``manage.py check`` and at ``manage.py runserver``.

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the routes ``manage.py routes`` lists.
   :doc:`/content/topics/permissions` for the rules ``manage.py permissions`` lists.
   :doc:`/content/topics/seo/auditing` for where the ``showmetadata`` command fits beside the checks and the tests.
   :doc:`metadata` for the metadata the command reads.
   :doc:`realtime` for the brokers, positions, presence and replay the ``realtime`` command reads.
