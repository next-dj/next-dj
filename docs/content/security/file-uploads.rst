.. _security-file-uploads:

File upload security
====================

An upload is the one request where a visitor writes bytes the server keeps and later hands to another visitor.
This page covers the limits, the validation, and the serving rules an upload needs, and states what the framework does and does not do on its own.

.. contents::
   :local:
   :depth: 2

What the framework does
-----------------------

The form action dispatch passes ``request.FILES`` into the form alongside ``request.POST``, so a ``FileField`` on a registered action binds exactly as it does in a plain Django view.
That pass-through is the whole of the framework's involvement.
The framework runs no size check, no content sniff, and no filename rewrite of its own, so every rule on this page is Django field validation plus project code.

Four framework behaviours around uploads are worth knowing before writing that code.

Actions are unauthenticated by default.
   The dispatch endpoint accepts a POST from any visitor, so an upload action without a guard lets an anonymous client write files.
   Declare ``Meta.login_required`` on the form, or a stricter guard, see :ref:`topics-forms-actions-guards`.

The inline validate pass never validates a file field.
   A field-level validate request runs the bound form and then drops every file field from the set of fields it reports on, so a file field's validators run only on the full submit.
   Both authorization layers run before that pass, so a guarded action does not expose its validator to an anonymous caller, yet a hand-written client can still send multipart bytes to it and have them parsed and discarded.
   The request-level limits below are what bound that work, because no row is ever written on the validate path.

Wizard steps hold no files.
   Wizard storage persists each step's ``cleaned_data`` between requests and an uploaded file does not survive that round trip.
   The ``next.W058`` check reports a ``FileField`` or ``ImageField`` in a static wizard step.

Uploaded media never travels through the static pipeline.
   The static collector serves only assets its scanner registered, and it reads nothing from ``MEDIA_ROOT``.
   How uploaded files reach a browser is entirely the project's choice, which is why `Serve media from its own origin`_ matters.

Limit the request before it is parsed
-------------------------------------

Two Django settings bound a multipart request, and neither one caps the total bytes of an upload.
Read :doc:`django:ref/settings` for the canonical semantics and :doc:`django:topics/http/file-uploads` for the parsing path they sit on.

``DATA_UPLOAD_MAX_MEMORY_SIZE``
   Bounds the non-file part of the body.
   The multipart parser counts the bytes of the plain fields and raises ``RequestDataTooBig`` past the limit, and it never adds the bytes of a file part to that count.
   The setting therefore stops a body padded with a million text fields and does nothing about a large file.

``FILE_UPLOAD_MAX_MEMORY_SIZE``
   Decides when a file part stops being buffered in memory and starts being streamed to a temporary file.
   It is a memory threshold rather than a ceiling, so raising it raises the memory one request can consume and lowering it does not refuse anything.

``DATA_UPLOAD_MAX_NUMBER_FILES`` caps how many file parts one request may carry and raises ``TooManyFilesSent`` past that count, which closes the request that ships ten thousand one-byte files.

A hard ceiling on upload bytes comes from two places instead.
The web server refuses the oversized request before Python sees it, and a validator on the field refuses what gets through.

.. code-block:: nginx
   :caption: nginx.conf

   client_max_body_size 10m;

.. code-block:: python
   :caption: config/settings.py

   DATA_UPLOAD_MAX_MEMORY_SIZE = 1 * 1024 * 1024
   DATA_UPLOAD_MAX_NUMBER_FILES = 10
   FILE_UPLOAD_MAX_MEMORY_SIZE = 512 * 1024

.. code-block:: python
   :caption: notes/validators.py

   from django.core.exceptions import ValidationError

   MAX_UPLOAD_BYTES = 5 * 1024 * 1024

   def validate_upload_size(uploaded):
       """Reject an upload past the per-field ceiling."""
       if uploaded.size > MAX_UPLOAD_BYTES:
           msg = "The file is larger than 5 MB."
           raise ValidationError(msg)

Read the bytes, not the declared type
-------------------------------------

:attr:`~django.core.files.uploadedfile.UploadedFile.content_type` is copied out of the multipart part header, so the client writes it.
A visitor uploading a payload names it ``image/png`` and Django reports ``image/png``.
The attribute is a hint for the user interface and never evidence about the contents.

The extension is client-supplied in the same way, and it still deserves an allow list because the extension is what a later web server reads when it picks a response ``Content-Type``.
:class:`~django.core.validators.FileExtensionValidator` holds that list.
Combine the three layers, an extension allow list, a media type read from the leading bytes, and a parse of the file by the library that owns the format.

.. code-block:: python
   :caption: notes/validators.py

   import magic
   from django.core.exceptions import ValidationError

   ALLOWED_TYPES = frozenset({"image/jpeg", "image/png", "application/pdf"})

   def sniffed_type(uploaded):
       """Return the media type read from the leading bytes of the upload."""
       head = uploaded.read(2048)
       uploaded.seek(0)
       return magic.from_buffer(head, mime=True)

   def validate_upload_type(uploaded):
       """Reject an upload whose own bytes fall outside the allow list."""
       if sniffed_type(uploaded) not in ALLOWED_TYPES:
           msg = "The file type is not accepted."
           raise ValidationError(msg)

.. note::

   ``python-magic``, imported above as ``magic``, and the system ``libmagic`` library it wraps are a third-party dependency, not shipped by next.dj.
   Install the package separately (``pip install python-magic``) and the platform ``libmagic`` package before enabling this validator.

.. code-block:: python
   :caption: notes/forms.py

   from django.core.validators import FileExtensionValidator
   from notes.models import Attachment
   from notes.validators import validate_upload_size, validate_upload_type

   from next.forms import ModelForm

   class AttachmentForm(ModelForm):
       class Meta:
           model = Attachment
           fields = ("title", "file")
           login_required = True

       def clean_file(self):
           uploaded = self.cleaned_data["file"]
           FileExtensionValidator(["jpg", "jpeg", "png", "pdf"])(uploaded)
           validate_upload_size(uploaded)
           validate_upload_type(uploaded)
           return uploaded

A sniffed media type raises the bar without clearing it, because a file can satisfy two formats at once.
For an image the strongest available check is a re-encode.
:class:`~django.forms.ImageField` already opens the upload with Pillow and calls ``verify()`` on it, which refuses anything Pillow cannot parse, and it overwrites ``content_type`` with the type Pillow recognised rather than the one the client declared.
A project that re-encodes the image itself and stores the output keeps nothing of the original bytes, which is the one approach a polyglot does not survive.

Never let the filename reach a path
-----------------------------------

:attr:`~django.core.files.uploadedfile.UploadedFile.name` is the filename the client chose.
It can carry ``..``, a leading separator, a null byte, a control character, a right-to-left override that disguises the extension, or several hundred characters of padding.
Three rules follow.

- Never interpolate the uploaded name into a filesystem path, a shell command, or an archive entry.
- Never open a file by a path a request supplies, on upload or on download.
- Generate the stored name on the server and keep the client name, if it is needed at all, in a database column.

A callable ``upload_to`` produces the stored name from server-side data, and the extension is the only thing it takes from the request.

.. code-block:: python
   :caption: notes/models.py

   import uuid
   from pathlib import Path

   from django.db import models

   def attachment_path(instance, filename):
       """Return a server-generated storage path for one attachment."""
       suffix = Path(filename).suffix.lower()[:10]
       return f"attachments/{uuid.uuid4().hex}{suffix}"

   class Attachment(models.Model):
       title = models.CharField(max_length=120)
       original_name = models.CharField(max_length=255, blank=True)
       file = models.FileField(upload_to=attachment_path)

Django's storage layer runs the returned name through its own sanitiser and raises ``SuspiciousFileOperation`` for a name that escapes the storage location, and it renames a colliding file rather than overwriting one.
Treat both as a backstop under the rule above, not as the rule.
See :doc:`django:topics/files` for the storage contract.

A client name shown back to a visitor is untrusted text.
Render it through the template engine and let auto escaping handle it, and never pass it through ``mark_safe`` or the ``safe`` filter.

Serve media from its own origin
-------------------------------

A stored file that the browser renders inline, from the same origin as the application, runs in the application's security context.
An uploaded HTML or SVG document then reads the session cookie, calls the same-origin endpoints the visitor is authorised for, and reports back.
The extension allow list is the first defence and the serving rules are the second, because an allow list that grows by one entry should not be the difference between a site and a compromise.

Serve user media from a hostname that shares no cookies with the application, for example ``usercontent.example.net`` rather than a subdomain of the application domain.
A cookie scoped to the application domain does not travel to a separate registrable domain, so a script that does run there reaches nothing.

Set two response headers on every media response.

``Content-Disposition: attachment``
   The browser downloads the file rather than rendering it, so no markup in it executes.
   Add a ``filename`` parameter built from the sanitised name when the download should keep a readable name.

``X-Content-Type-Options: nosniff``
   The browser honours the declared ``Content-Type`` instead of guessing one from the bytes, so a text file full of markup is not promoted to HTML.
   ``SECURE_CONTENT_TYPE_NOSNIFF = True`` sends the header for responses Django serves, and the media origin needs its own copy of it because those responses usually come from the web server.

.. code-block:: nginx
   :caption: nginx.conf

   location /media/ {
       root /srv/notes;
       add_header Content-Disposition "attachment" always;
       add_header X-Content-Type-Options "nosniff" always;
       add_header Content-Security-Policy "default-src 'none'; sandbox" always;
   }

The ``static`` helper that serves ``MEDIA_ROOT`` while ``DEBUG`` is on sets none of this.
It is a development convenience and belongs behind a ``DEBUG`` guard, see :ref:`howto-file-uploads`.

Private files need an authorised view rather than a public URL.
A view that checks ownership and then streams the file keeps the check on the request path, and a signed expiring URL from the storage backend does the same for a large object.
A random filename is not access control, because a URL is shared, logged, and sent in a referrer.

SVG executes script
-------------------

An SVG is an XML document the browser renders with scripting enabled.
A ``<script>`` element inside it, or an ``onload`` attribute on any of its elements, runs when the browser renders the file inline.
An accepted SVG upload served inline from the application origin is therefore stored cross-site scripting, and it needs no exotic trick to get there.

:class:`~django.forms.ImageField` refuses SVG on its own, because Pillow has no SVG decoder and the parse fails.
The exposure comes from a plain ``FileField`` with an extension list that includes ``svg``, and from a project that accepts SVG deliberately for logos and icons.

Take one of three positions.

Refuse SVG.
   Leave ``svg`` out of the extension allow list and out of the sniffed type allow list.
   This is the right default, and a raster upload path loses nothing by it.

Serve SVG as a download from the media origin.
   ``Content-Disposition: attachment`` on a separate hostname means the file never renders in the application's context.
   The uploaded SVG is then storage, not a displayable asset.

Sanitise the XML and keep it off the application origin.
   Parse the document server-side, drop every script element, every event-handler attribute, every ``<foreignObject>``, and every external reference, then serialise what survives and store that.
   Use a maintained sanitiser rather than a regular expression, and configure the XML parser to resolve no external entities and no DTD, because an uploaded SVG also reaches an XML parser and an entity expansion attack reads server files or exhausts memory.

Never inline an uploaded SVG into a template through ``mark_safe`` or the ``safe`` filter, and never through an ``<svg>`` element built from stored markup.
Referencing the file from an ``<img>`` element blocks its scripts in current browsers, which makes that the safer of the two inline options, and it is still no substitute for the positions above.

Checklist
---------

- The upload action carries a guard, so an anonymous client cannot write files.
- The web server caps the request body, and a field validator caps the file.
- ``DATA_UPLOAD_MAX_MEMORY_SIZE``, ``DATA_UPLOAD_MAX_NUMBER_FILES``, and ``FILE_UPLOAD_MAX_MEMORY_SIZE`` carry values the project chose.
- The extension is checked against an allow list and the media type is read from the bytes.
- Images are parsed, and ideally re-encoded, by the library that owns the format.
- The stored name is generated on the server and the client name lives in a column.
- User media is served from a hostname that shares no cookies with the application.
- Every media response carries ``Content-Disposition: attachment`` and ``X-Content-Type-Options: nosniff``.
- SVG is refused, or sanitised and served as a download.
- Private files pass through a view that checks ownership.

See also
--------

.. seealso::

   :doc:`overview` for the wider threat model.
   :doc:`csrf-and-forms` for the token on the upload POST.
   :doc:`static-assets` for the shipped assets, which are a separate pipeline from user media.
   :ref:`howto-file-uploads` for the working upload form.
   :doc:`django:topics/http/file-uploads` for Django's upload handling.
