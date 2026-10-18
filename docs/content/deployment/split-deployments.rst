.. _deployment-split-deployments:

Split deployments
=================

One code base can ship as several deployments, each with a settings module of its own.
A public site and a back office, or a core service and a context that runs on its own machines, share the applications and differ in which of them each one installs and which URLs it serves.
This page covers what such a split asks of the settings and of CI.
:doc:`/content/howto/split-settings-per-environment` covers the settings package the deployments build on.

.. contents::
   :local:
   :depth: 1

Settings per deployment
-----------------------

Each deployment is one settings module over a shared base, with its own ``INSTALLED_APPS`` and its own ``ROOT_URLCONF``.

.. code-block:: python
   :caption: config/settings/backoffice.py

   from config.settings.base import *

   INSTALLED_APPS = [
       *SHARED_APPS,
       "ledger.core",
       "ledger.identity.accounts",
       "ledger.money.wallets",
       "ledger.reporting.ledgers",
   ]
   ROOT_URLCONF = "config.urls_backoffice"

``SHARED_APPS`` in ``base.py`` lists the Django and next.dj applications every deployment needs, ``"next"`` among them.
The file router walks the page trees of the installed applications alone, so the routes of a deployment follow its ``INSTALLED_APPS``, and ``manage.py next --settings=config.settings.backoffice routes`` lists them.
The ``DJANGO_SETTINGS_MODULE`` of each process, or ``--settings`` on each command, picks the deployment.

Closure
-------

A deployment is closed when every application it installs finds what it depends on among the applications it installs.
An application that misses a dependency boots fine and fails at the first request that reaches the missing code, so the check has to find it first.

- ``next.E174`` reports a ``depends_on`` entry that the settings do not install, and names the settings module, under ``manage.py check --tag boundaries`` alone, see :ref:`topics-boundaries-closure`.
- Django reports a relation declared by a string into an application the settings do not install as ``fields.E300`` and ``fields.E307`` in a plain ``manage.py check``, see :doc:`django:ref/checks`.
- A relation declared by an imported model class into such an application stops ``django.setup()`` with a ``RuntimeError`` before any check runs.

``depends_on`` is what makes the first item possible, since without it the check has no way to know what an application expects to find.
A project that splits its deployments declares ``depends_on`` on every application first, as :doc:`/content/howto/scale-to-contexts` shows.

The ratchet across deployments
------------------------------

The boundary scan reads the applications the settings install, while ``boundaries.toml`` records the debt of the whole project.
A run counts as a reduced deployment when ``boundaries.toml`` records a file that is on disk and that no installed application of the project holds, or when a ``depends_on`` names an application the settings do not install.
The check reads only the records and the declarations to decide it, and never walks the directory tree, so a stray package under ``BASE_DIR`` changes nothing.

A reduced deployment cannot tell whether a finding is gone because it was fixed or because its edge reached an application the settings drop, so it judges growth alone.
A file that holds more findings than its record is still ``next.E168``, a file on disk that holds fewer is no error, and the records of the files no installed application holds are set aside.
A recorded file that no longer exists is ``next.E169`` in every deployment.
``manage.py next boundaries --refreeze`` under such settings keeps every record of a file on disk as it is and only drops the records of deleted files, so the record of the whole project never shrinks behind its back.

The settings that install every application hold the full ratchet, ``next.E169`` for a fixed finding included, so CI runs the boundary checks under them.
One case stays invisible.
When an installed application loses a recorded finding only because its edge reached a dropped application, and no record and no ``depends_on`` entry points at that dropped application, the run looks whole and reports ``next.E169``.
Declaring ``depends_on`` on the remaining application makes the drop visible, which is one more reason a project that splits its deployments declares it everywhere.

Checks in CI
------------

CI runs the checks once per deployment, with the settings of each.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check --tag boundaries --fail-level WARNING --settings=config.settings.full
   uv run python manage.py check --tag boundaries --fail-level WARNING --settings=config.settings.site
   uv run python manage.py check --tag boundaries --fail-level WARNING --settings=config.settings.backoffice
   uv run python manage.py check --deploy --settings=config.settings.site
   uv run python manage.py check --deploy --settings=config.settings.backoffice

The first line holds the boundaries and the full ratchet under the settings that install every application.
The next two run ``next.E174`` for each deployment, since the boundary checks run only under their tag, and ``--fail-level WARNING`` keeps a warning of the ``"warn"`` mode from passing in silence.
The last two run Django's relation checks with the deployment checks of :doc:`checklist`.
A deployment that no CI step checks is the one whose missing dependency reaches production.

See also
--------

.. seealso::

   :doc:`checklist` for the full pre-flight list.
   :doc:`/content/topics/boundaries` for ``depends_on`` and the ratchet.
   :doc:`/content/howto/split-settings-per-environment` for the settings package.
   :doc:`django:topics/settings` for ``DJANGO_SETTINGS_MODULE`` and ``--settings``.
