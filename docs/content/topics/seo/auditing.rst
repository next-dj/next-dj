.. _topics-seo-auditing:

Auditing metadata
=================

Two tiers of system checks read the metadata of a project.
The default tier runs on every ``manage.py check`` and reports a declaration the framework cannot render as intended.
The content audits run only on request and grade the folded result the way a search engine would.
This page covers what each tier reads, how to turn the audits on, and where the thresholds live.

.. contents::
   :local:
   :depth: 2

What the checks can see
-----------------------

Every check reads the static fold of a page, the settings tier plus every ``metadata`` dict along its chain.
A ``@page.metadata`` callable contributes nothing to that fold, because running user code at check time could reach a database the deploy has yet to migrate, so the checks validate the callable's shape and leave its values to the request.
The title and description audits, ``next.W089`` to ``next.W092``, skip every page whose chain carries a callable, since the static tier of such a page is not what it renders.
A duplicate title two rows produce at runtime is therefore a matter for the test suite rather than for the audit.

The default tier
----------------

The default tier is the set of ``next.E098`` to ``next.E109`` and ``next.W084`` to ``next.W088``.
It reports a settings scope with an unknown option or a malformed ``DEFAULTS``, a title template with a placeholder outside ``{title}`` and ``{site_name}``, a template without a default, a ``base`` that is not an origin, an empty title, a ``page.py`` carrying both a dict and a callable, a dict with an unknown key, and a callable declared in the wrong file or not annotated to return a mapping.
The warnings cover a page whose composed template never renders ``{% metadata %}``, a root-relative canonical or image with no base outside ``DEBUG``, an ``alternates.languages=True`` without :func:`~django.conf.urls.i18n.i18n_patterns`, and a ``noindex`` page whose canonical points elsewhere.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

The sitemap and robots checks, ``next.E110`` to ``next.E117`` and ``next.W097`` to ``next.W103``, belong to the same default tier and carry the ``urls`` tag beside ``next``.
They read the ``sitemap.py``, ``robots.py``, and ``robots.txt`` at the top of every page root without a request, so a file that fails to import, an ``@sitemap.items`` trail the tree does not route, a second robots source, a ``Disallow`` covering a listed URL or a ``noindex`` page, and a route the host root does not resolve are all reported here.
:doc:`sitemaps` and :doc:`robots` name each condition where the behaviour it guards is described.

The conditions and the emitting module of every code are tabulated in :doc:`/content/ref/system-checks`.

The content audits
------------------

The audits are ``next.W089`` to ``next.W096``.
They carry ``deploy=True`` and the ``seo`` tag, so a plain ``manage.py check`` never runs them, ``manage.py check --deploy`` runs them beside the other deployment checks, and the tag narrows a run to the audits alone.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check --deploy --tag seo

The audits warn about a page without a description, a description outside 50 to ``DESCRIPTION_MAX`` characters, a title over ``TITLE_MAX`` characters, two routes folding to the same title, a same-origin canonical that resolves to no URL of the project, a literal canonical on a dynamic route, an hreflang mapping without an ``x-default``, and an hreflang code ``LANGUAGES`` does not list.
Titles and descriptions are measured under ``LANGUAGE_CODE``, so a translation that runs long in another language is not caught here.

.. mermaid::

   flowchart LR
       Plain["manage.py check"] --> Default["E098 to E117, W084 to W088, W097 to W103"]
       Deploy["manage.py check --deploy"] --> Default
       Deploy --> Audits["W089 to W096"]
       Tagged["manage.py check --deploy --tag seo"] --> Audits

Thresholds
----------

``NEXT_FRAMEWORK["METADATA"]["CHECKS"]`` holds the numbers the audits compare against.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "METADATA": {
           "CHECKS": {
               "TITLE_MAX": 60,
               "DESCRIPTION_MAX": 160,
               "REQUIRE_DESCRIPTION": True,
           },
       },
   }

The values shown are the defaults.
``REQUIRE_DESCRIPTION`` set to ``False`` silences ``next.W089`` for a project whose pages describe themselves through their body, and the length audit still runs on every description that is declared.
A threshold holding anything but an integer falls back to its default.

Opting out
----------

A warning that names a deliberate shape is silenced by its id through Django's ``SILENCED_SYSTEM_CHECKS``, which drops the message and keeps counting it.
A landing page and its ``/index/`` alias sharing one title, for instance, silence ``next.W090`` and nothing else.
The setting can only drop a check, so the audits are turned on by the ``--deploy`` flag and by nothing in ``NEXT_FRAMEWORK``.

.. code-block:: python
   :caption: config/settings.py

   SILENCED_SYSTEM_CHECKS = ["next.W090"]

See also
--------

.. seealso::

   :doc:`/content/howto/audit-seo-before-deploy` for wiring the audit into CI and the deploy script.
   :doc:`/content/ref/system-checks` for every code, its condition, and the ``seo`` tag mechanics.
   :doc:`/content/ref/settings` for the ``METADATA`` scope.
