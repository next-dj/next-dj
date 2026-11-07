.. _howto-django-admin:

Integrate Django admin
======================

Problem
-------

You want the :doc:`Django admin <django:ref/contrib/admin/index>` and next.dj pages to coexist in the same project.
The admin uses Django URL patterns and templates while the pages live under the file router.

Solution
--------

Mount the admin under a path prefix above the file router in ``config/urls.py``.
The Django URL resolver tries the admin patterns first.
Anything that does not match falls through to next.dj.

Walkthrough
-----------

Register the admin in ``urls.py``.

.. code-block:: python
   :caption: config/urls.py

   from django.contrib import admin
   from django.urls import include, path

   urlpatterns = [
       path("admin/", admin.site.urls),
       path("", include("next.urls")),
   ]

Anything starting with ``/admin/`` reaches the admin.
Every other URL reaches the file router.

Confirm INSTALLED_APPS
~~~~~~~~~~~~~~~~~~~~~~

Add the admin and the auth apps that the admin depends on.
The caption below assumes a single settings module.
A project that split its settings places this in ``config/settings/base.py`` instead.

.. code-block:: python
   :caption: config/settings.py

   INSTALLED_APPS = [
       "django.contrib.admin",
       "django.contrib.auth",
       "django.contrib.contenttypes",
       "django.contrib.sessions",
       "django.contrib.messages",
       "next",
       "django.contrib.staticfiles",
       "notes",
   ]

``next`` stays above ``django.contrib.staticfiles`` so its ``runserver`` wins, as :ref:`the install guide explains <intro-install-app-order>`.

Register models
~~~~~~~~~~~~~~~

Standard Django admin registration applies.

.. code-block:: python
   :caption: notes/admin.py

   from django.contrib import admin
   from notes.models import Note

   @admin.register(Note)
   class NoteAdmin(admin.ModelAdmin):
       list_display = ("title", "created_at")

Link to admin from the site
---------------------------

Use ``reverse`` from inside the file router.

.. code-block:: python
   :caption: notes/pages/page.py

   from django.urls import reverse

   from next import context

   @context("admin_url")
   def admin_url() -> str:
       return reverse("admin:notes_note_changelist")

Object permissions in the admin
-------------------------------

List ``ObjectPermissionBackend`` next to ``ModelBackend`` in ``AUTHENTICATION_BACKENDS`` to let the admin see the permissions that ``permissions.py`` registers.
The admin index calls ``has_module_perms`` and ``get_all_permissions()``, which ask every registered predicate with ``obj=None``, so a predicate must handle ``None`` before it reads ``obj``.
See :ref:`topics-permissions` for the predicate rules.

The ``admin`` example rebuilds the admin pages on next.dj, see :doc:`/content/misc/examples`.

Verification
------------

Run migrations and create a superuser.

.. code-block:: bash
   :caption: shell

   uv run python manage.py migrate
   uv run python manage.py createsuperuser

Start the server and visit ``/admin/``.
The admin renders, the existing next.dj routes continue to work, and there is no overlap between the two URL spaces.

See also
--------

.. seealso::

   :doc:`/content/topics/permissions` for object permissions.
   :doc:`/content/misc/examples` for the ``admin`` example, which rebuilds the admin pages on next.dj.
