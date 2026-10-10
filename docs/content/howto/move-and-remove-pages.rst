.. _howto-move-and-remove-pages:

Move and remove pages
=====================

Problem
-------

A page that moves to a new URL should pass its ranking on with a permanent redirect, and a page that is gone for good should answer 410 so search engines drop it quickly rather than retrying a 404.

Solution
--------

Install :doc:`django.contrib.redirects <django:ref/contrib/redirects>`, whose middleware answers a 404 with the redirect or the 410 a ``Redirect`` row names, record a row whenever a page directory moves or a slug changes, and let the sitemap drop the old URL on its own.

Walkthrough
-----------

Install the redirects application
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The application needs ``django.contrib.sites`` and a ``SITE_ID``, and its middleware sits near the end of the stack so it sees the final 404.

.. code-block:: python
   :caption: config/settings.py

   INSTALLED_APPS = [
       "django.contrib.sites",
       "django.contrib.redirects",
       "next",
       "blog",
   ]

   SITE_ID = 1

   MIDDLEWARE = [
       "django.middleware.security.SecurityMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
       "django.middleware.common.CommonMiddleware",
       "django.contrib.redirects.middleware.RedirectFallbackMiddleware",
   ]

Run ``migrate`` to create the table, and set ``NEXT_FRAMEWORK["SITE"]["URL"]`` as well, since the sitemap otherwise reads the ``Site`` row, which ships as ``example.com``.

Move a page directory
~~~~~~~~~~~~~~~~~~~~~

Renaming ``blog/pages/guides/`` to ``blog/pages/docs/`` moves every URL under it, and a data migration records the old addresses.

.. code-block:: python
   :caption: blog/migrations/0007_redirect_guides.py

   from django.db import migrations

   def forward(apps, schema_editor):
       Redirect = apps.get_model("redirects", "Redirect")
       Redirect.objects.update_or_create(site_id=1, old_path="/guides/", defaults={"new_path": "/docs/"})
       Redirect.objects.update_or_create(site_id=1, old_path="/guides/setup/", defaults={"new_path": "/docs/setup/"})

   class Migration(migrations.Migration):
       dependencies = [("blog", "0006_post_slug"), ("redirects", "0002_alter_redirect_new_path_help_text")]
       operations = [migrations.RunPython(forward, migrations.RunPython.noop)]

``/guides/setup/`` now answers ``301 Moved Permanently`` with ``Location: /docs/setup/``.

Follow a renamed slug
~~~~~~~~~~~~~~~~~~~~~

A dynamic route whose row changes its slug records the move when it saves.

.. code-block:: python
   :caption: blog/models.py

   from django.contrib.redirects.models import Redirect
   from django.contrib.sites.models import Site
   from django.db import models

   class Post(models.Model):
       slug = models.SlugField(unique=True)

       def save(self, *args, **kwargs):
           old = type(self).objects.filter(pk=self.pk).values_list("slug", flat=True).first()
           super().save(*args, **kwargs)
           if old and old != self.slug:
               Redirect.objects.update_or_create(
                   site=Site.objects.get_current(),
                   old_path=f"/posts/{old}/",
                   defaults={"new_path": f"/posts/{self.slug}/"},
               )

The page's ``@context`` callable raises :exc:`~django.http.Http404` for the old slug, the middleware finds the row, and the visitor is redirected to the new URL.

Remove a page for good
~~~~~~~~~~~~~~~~~~~~~~

A row with an empty ``new_path`` answers ``410 Gone``.

.. code-block:: python
   :caption: blog/admin_actions.py

   from django.contrib.redirects.models import Redirect
   from django.contrib.sites.models import Site

   def retire(post):
       Redirect.objects.update_or_create(
           site=Site.objects.get_current(), old_path=f"/posts/{post.slug}/", defaults={"new_path": ""}
       )
       post.delete()

Delete the page directory for a static page and add the row the same way.
The sitemap drops the URL by itself, the static route with its directory and the dynamic one with its row, so no crawler is invited to a redirect.

Verification
------------

.. code-block:: bash
   :caption: shell

   curl -sI https://blog.example/guides/setup/ | head -2
   curl -sI https://blog.example/posts/retired-post/ | head -1
   curl -s https://blog.example/sitemap.xml | grep -c "/guides/"

The moved page answers ``301`` with the new ``Location``, the retired one ``410``, and the sitemap lists no ``/guides/`` URL, so no crawler is sent to a redirect.

See also
--------

.. seealso::

   :doc:`/content/topics/seo/sitemaps` for how routes and rows reach the sitemap.
   :doc:`/content/topics/pages` for a ``render()`` that answers a redirect of its own.
   :doc:`django:ref/contrib/redirects` for the middleware and the admin.
