.. _howto-file-uploads:

Handle File Uploads
===================

Problem
-------

You want a form that accepts a file upload and saves the file alongside a model instance.

Solution
--------

Add a ``FileField`` or ``ImageField`` to the form, render the form with ``enctype="multipart/form-data"``, and call ``form.save()`` from the handler.
See Django's :doc:`file uploads <django:topics/http/file-uploads>` and :doc:`managing files <django:topics/files>` for how Django stores the uploaded data.

Walkthrough
-----------

Define the model.

.. code-block:: python
   :caption: notes/models.py

   from django.db import models


   class Attachment(models.Model):
       title = models.CharField(max_length=120)
       file = models.FileField(upload_to="attachments/")

Define the form.

.. code-block:: python
   :caption: notes/forms.py

   from next.forms import ModelForm

   from notes.models import Attachment


   class AttachmentForm(ModelForm):
       class Meta:
           model = Attachment
           fields = ("title", "file")

Register the action.

.. code-block:: python
   :caption: notes/routes/attachments/page.py

   from django.http import HttpResponseRedirect
   from django.urls import reverse

   from next.forms import action

   from notes.forms import AttachmentForm


   @action("upload_attachment", form_class=AttachmentForm)
   def upload_attachment(form: AttachmentForm) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect(reverse("next:page_attachments"))

Render the form with the right encoding type.

.. code-block:: jinja
   :caption: notes/routes/attachments/template.djx

   {% form @action="upload_attachment" enctype="multipart/form-data" %}
     {{ form.title }}
     {{ form.file }}
     <button type="submit">Upload</button>
   {% endform %}

The ``enctype`` attribute is required.
Without it the browser submits only text values and ``form.file`` is empty.

Configure media storage.

A ``FileField`` writes to ``MEDIA_ROOT`` under the ``upload_to`` subdirectory, and the saved URL is built from ``MEDIA_URL``.
Both settings must be present before any upload is saved.

.. code-block:: python
   :caption: config/settings.py

   MEDIA_ROOT = BASE_DIR / "media"
   MEDIA_URL = "/media/"

Serve uploaded files in development.

The file router include lives in the root URLconf alongside Django's :func:`~django.conf.urls.static.static` helper, which exposes ``MEDIA_ROOT`` while ``DEBUG`` is on.
A production deployment serves the same files through the web server instead.

.. code-block:: python
   :caption: config/urls.py

   from django.conf import settings
   from django.conf.urls.static import static
   from django.urls import include, path


   urlpatterns = [
       path("", include("next.urls")),
   ]

   if settings.DEBUG:
       urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

Verification
------------

Submit the form with a file and confirm that the model row is created.
Inspect ``MEDIA_ROOT`` and verify the file appears under ``attachments/``.

Tests
-----

Use ``SimpleUploadedFile`` to feed a fake file into ``NextClient``.

.. code-block:: python
   :caption: tests/test_upload.py

   from django.core.files.uploadedfile import SimpleUploadedFile

   from next.testing.client import NextClient


   def test_upload(db) -> None:
       fake = SimpleUploadedFile("file.txt", b"hello")
       response = NextClient().post_action(
           "upload_attachment",
           {"title": "First", "file": fake},
       )
       assert response.status_code == 302

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/actions` for handler patterns.
   :doc:`/content/topics/forms/templates` for the form tag.
