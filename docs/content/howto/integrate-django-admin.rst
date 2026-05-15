.. _howto-django-admin:

Integrate Django Admin
======================

Problem
-------

You want Django admin and next.dj pages to coexist in the same project.
The admin uses Django URL patterns and templates while the pages live under the file router.

Solution
--------

Mount the admin under a path prefix above the file router in ``config/urls.py``.
The Django URL resolver tries the admin patterns first, anything that does not match falls through to next.dj.

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

.. code-block:: python
   :caption: config/settings.py

   INSTALLED_APPS = [
       "django.contrib.admin",
       "django.contrib.auth",
       "django.contrib.contenttypes",
       "django.contrib.sessions",
       "django.contrib.messages",
       "django.contrib.staticfiles",
       "next",
       "notes",
   ]

Register Models
~~~~~~~~~~~~~~~

Standard Django admin registration applies.

.. code-block:: python
   :caption: notes/admin.py

   from django.contrib import admin

   from notes.models import Note


   @admin.register(Note)
   class NoteAdmin(admin.ModelAdmin):
       list_display = ("title", "created_at")

Link to Admin From the Site
---------------------------

Use ``reverse`` from inside the file router.

.. code-block:: python
   :caption: notes/routes/page.py

   from django.urls import reverse

   from next.pages import context


   @context("admin_url")
   def admin_url() -> str:
       return reverse("admin:notes_note_changelist")

Use Frozen Form Specs Inside Admin
----------------------------------

When the admin renders a custom form, ``next.forms.serializers.form_spec`` produces a frozen descriptor that admin templates can render without touching the standard Django widgets.
See ``examples/admin`` in the repository for the full walkthrough.

Verification
------------

Run migrations and create a superuser.

.. code-block:: bash
   :caption: shell

   uv run python manage.py migrate
   uv run python manage.py createsuperuser

Start the server and visit ``/admin/``.
The admin renders, the existing next.dj routes continue to work, and there is no overlap between the two URL spaces.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/serializers` for frozen form specs.
   ``examples/admin`` for the worked integration.
