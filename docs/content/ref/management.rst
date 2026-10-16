.. _ref-management:

Management commands
===================

Module summary
--------------

The ``next`` application ships two management commands.
``next`` dispatches the next.dj subcommands, and ``showmetadata`` is a thin shell over the metadata of ``next.pages``.

next
----

``manage.py next <subcommand>`` runs one subcommand, and ``manage.py next <subcommand> --help`` describes it.
Each module of ``next.management.subcommands`` whose name does not start with an underscore is a subcommand named after the module.

Django's own options, such as ``--settings`` and ``--skip-checks``, belong to ``next`` and go before the subcommand name, because the parser of a subcommand does not know them.
Like any Django command, ``next`` runs the system checks before the subcommand, so ``--skip-checks`` prints the table of a project whose checks fail.

.. code-block:: bash
   :caption: Django options before the subcommand

   uv run python manage.py next --skip-checks routes

routes
~~~~~~

``manage.py next routes`` prints one row per routed page, in resolution order, with five columns.

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

.. code-block:: text
   :caption: manage.py next routes

   URL                                Name                                     App      File                        Source
   /staff/                            audit:page_                              audit    audit/pages/page.py         urls.py
   /staff/log/                        audit:page_log                           audit    audit/pages/log/page.py     urls.py
   /wallets/                          next:page_wallets                        wallets  wallets/pages/page.py       mounts
   /wallets/<int:wallet>/operations/  next:page_wallets_int_wallet_operations  wallets  wallets/operations/page.py  mounts
   /                                  next:page_                               notes    notes/pages/page.py         root

A tree of ``next.urls`` is listed only when the root URLconf includes ``next.urls``.
The table lists exactly the routed pages, so a page whose route the URL parser refuses and a ``page.py`` with no body source to render are left out.
Under :func:`~django.conf.urls.i18n.i18n_patterns` a URL carries the prefix of ``LANGUAGE_CODE``.
:ref:`topics-file-router-mounting` covers the three sources.

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
   :doc:`/content/topics/seo/auditing` for where the ``showmetadata`` command fits beside the checks and the tests.
   :doc:`metadata` for the metadata the command reads.
