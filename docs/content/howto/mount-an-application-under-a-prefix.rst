.. _howto-mount-an-application-under-a-prefix:

Mount an application under a prefix
===================================

Problem
-------

The pages of an application should answer under a URL prefix such as ``/wallets/``, without repeating that prefix as the first directory of its page tree.
A second tree of the same application needs a prefix that captures a parameter, and the project may want one tree somewhere else entirely.

Solution
--------

Declare the prefix on the ``AppConfig`` through ``mount`` or ``mounts``, and keep the single ``include("next.urls")`` in the root URLconf.
Override the declaration for one tree from a ``urls.py`` through ``pages()`` when the project decides where that tree lives.
:ref:`topics-file-router-mounting` describes the three levels in full.

Walkthrough
-----------

Declare the prefix
~~~~~~~~~~~~~~~~~~

Set ``mount`` on the configuration class of the application.

.. code-block:: python
   :caption: wallets/apps.py

   from django.apps import AppConfig

   class WalletsConfig(AppConfig):
       default_auto_field = "django.db.models.BigAutoField"
       name = "wallets"
       label = "wallets"
       mount = "wallets/"

``wallets/pages/page.py`` now answers ``/wallets/`` and ``wallets/pages/[int:pk]/page.py`` answers ``/wallets/<int:pk>/``.
The prefix is relative to where the root URLconf includes ``next.urls``, so the project URLconf stays as it is.

Mount a second tree with a parameter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Replace ``mount`` with ``mounts``, which maps each subdirectory of the application to its prefix in :func:`~django.urls.path` syntax.

.. code-block:: python
   :caption: wallets/apps.py

   from django.apps import AppConfig

   class WalletsConfig(AppConfig):
       default_auto_field = "django.db.models.BigAutoField"
       name = "wallets"
       label = "wallets"
       mounts = {
           "pages": "wallets/",
           "operations": "wallets/<int:wallet>/operations/",
       }

The ``operations/`` directory sits beside ``pages/`` and holds a page tree of its own.
The ``wallet`` value the prefix captures reaches each page of that tree as a URL kwarg.

.. code-block:: python
   :caption: wallets/operations/page.py

   from wallets.models import Operation, Wallet

   from next import context
   from next.urls import DUrl

   @context("wallet")
   def current_wallet(wallet: DUrl[int]) -> Wallet:
       return Wallet.objects.get(pk=wallet)

   @context("operations")
   def wallet_operations(wallet: DUrl[int]) -> list[Operation]:
       return list(Operation.objects.filter(wallet_id=wallet))

A directory under ``operations/`` must not capture ``wallet`` again, which ``manage.py check`` reports as :ref:`next.E154 <ref-system-checks>`.

Reverse the mounted pages
~~~~~~~~~~~~~~~~~~~~~~~~~

The URL names of a mounted tree include the prefix, with each converter read as a bracket segment.

.. code-block:: python
   :caption: reversing under a prefix

   from next.urls import page_reverse

   page_reverse("wallets")                                    # "/wallets/"
   page_reverse("wallets/[int:wallet]/operations", wallet=1)  # "/wallets/1/operations/"

Move one tree from the URLconf
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``pages()`` mounts one tree wherever a ``urls.py`` includes it, and that placement wins over the declaration of the application for that tree alone.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   from next.urls import pages

   urlpatterns = [
       path("manage/wallets/<int:wallet>/operations/", pages("wallets", "operations")),
       path("", include("next.urls")),
   ]

The ``operations`` tree now answers under ``/manage/wallets/<int:wallet>/operations/``, while the ``pages`` tree stays under ``/wallets/``.
Drop the ``operations`` key from ``mounts`` as well, since the check :ref:`next.W127 <ref-system-checks>` warns about a declaration the URLconf overrides.
The pages of a ``pages()`` tree are named in the namespace of the application label and without the prefix.
Without the second argument, ``pages("wallets")`` mounts the ``PAGES_DIR`` tree of the application, ``pages/`` with the default setting.

.. code-block:: python
   :caption: reversing a pages() tree

   from next.urls import page_reverse

   page_reverse("", namespace="wallets", wallet=1)  # "/manage/wallets/1/operations/"

Verification
------------

List the routes and the level each tree mounts at.

.. code-block:: bash
   :caption: shell

   uv run python manage.py next routes

The ``Source`` column reads ``mounts`` for the ``pages`` tree and ``urls.py`` for the ``operations`` tree, and the ``URL`` column shows each prefix.
Run ``uv run python manage.py check`` to confirm that no declaration is refused and no route conflicts with another.

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the priority between the three levels and the route order.
   :doc:`/content/topics/project-layout` for where prefixes fit as a project grows.
   :doc:`/content/ref/management` for the columns of ``next routes``.
