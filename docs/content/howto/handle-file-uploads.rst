.. _howto-file-uploads:

Handle File Uploads
===================

Problem
-------

You want a form that accepts a file upload and saves the file alongside a model instance.

Solution
--------

Add a ``FileField`` or ``ImageField`` to the form, render it with the ``{% form %}`` tag, and call ``form.save()`` from the handler.
The tag emits ``enctype="multipart/form-data"`` on its own for any multipart form.
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
   from next.urls import page_reverse_lazy
   from notes.models import Attachment

   class AttachmentForm(ModelForm):
       class Meta:
           model = Attachment
           fields = ("title", "file")
           success_url = page_reverse_lazy("attachments")

``AttachmentForm`` registers automatically as ``attachment_form`` via autodiscovery on startup.
No manual import is needed in the page module.
No ``on_valid`` override is needed either: the default ``ModelForm`` implementation saves the instance and redirects to ``Meta.success_url``.
Without ``success_url`` the submission redirects back to the origin page.
See :ref:`topics-forms-actions-success` for the redirect contract.

Render the form.

.. code-block:: jinja
   :caption: notes/pages/attachments/template.djx

   {% form "attachment_form" %}
     {{ form.title }}
     {{ form.file }}
     <button type="submit">Upload</button>
   {% endform %}

The ``{% form %}`` tag detects that ``form.file`` makes the form multipart and emits ``enctype="multipart/form-data"`` on the ``<form>`` element automatically.
An explicit ``enctype="..."`` argument on the tag overrides the automatic value, and no upload form needs that.
The tag also emits the CSRF token and the hidden ``_next_form_origin`` field on its own, so the template adds nothing else.

.. warning::

   Render the file input through the plain Django widget, ``{{ form.file }}``, or a hand-written ``<input type="file" name="file">``.
   ``ComponentWidget`` does not support ``FileField``, and the ``next.W055`` system check warns about that pairing at startup.
   See :doc:`/content/topics/forms/field-components` for the widget limitations.

.. note::

   Keep file fields out of ``FormWizard`` steps.
   Wizard storage persists each step's ``cleaned_data`` between requests, and an uploaded file does not survive that round trip.
   The ``next.W058`` check flags a ``FileField`` or ``ImageField`` in a static wizard step, so collect the upload in a standalone form action like the one on this page.

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

A test feeds a fake file into ``NextClient`` with ``SimpleUploadedFile``.
A valid submission redirects.
The failing test names the origin with ``origin="/attachments/"`` so the dispatcher can resolve that path and re-render the page with the missing-file error.
The path must belong to a routed page, here the attachments page from the walkthrough.
Without a resolvable origin the invalid submission is rejected with HTTP 400, see :doc:`test-a-page-with-actions` for both branches.

.. code-block:: python
   :caption: tests/test_upload.py

   from django.core.files.uploadedfile import SimpleUploadedFile
   from next.testing.client import NextClient

   def test_upload(db) -> None:
       fake = SimpleUploadedFile("file.txt", b"hello")
       response = NextClient().post_action(
           "attachment_form",
           {"title": "First", "file": fake},
       )
       assert response.status_code == 302

   def test_upload_without_file_rerenders(db) -> None:
       response = NextClient().post_action(
           "attachment_form",
           {"title": "First"},
           origin="/attachments/",
       )
       assert response.status_code == 200
       assert b"This field is required" in response.content

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/actions` for handler patterns.
   :doc:`/content/topics/forms/templates` for the form tag.
