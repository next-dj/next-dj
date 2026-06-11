.. _security-di-untrusted:

DI and Untrusted Input
======================

The dependency resolver injects values from URL parameters, query strings, form bodies, and named providers.
This page covers the safety rules that apply to each source and the patterns that prevent injection vulnerabilities.

.. contents::
   :local:
   :depth: 2

Untrusted by Default
--------------------

Every value the resolver injects starts as untrusted input.
Three rules apply.

Validate before use.
   Run captured parameters through Django form validation, custom validators, or explicit type checks.

Whitelist not blacklist.
   Decide what a value can be, reject everything else.
   A directory named with the typed segment form such as ``[int:id]`` rejects non integer captures with ``404``.

Never interpolate into queries.
   Use the :doc:`ORM <django:topics/db/queries>` and parameter substitution.
   :doc:`Raw SQL <django:topics/db/sql>` needs ``cursor.execute(sql, params)``.

URL Parameters
--------------

``DUrl[T]`` coerces the captured value to ``T`` for ``int``, ``float``, ``bool``, ``UUID``, ``Decimal``, ``date``, and ``datetime``.
A value that fails to parse falls through as the raw string, so the page module still runs.
Use a directory named with the typed segment form such as ``[int:id]`` or ``[uuid:id]`` to reject a malformed capture with ``404`` before the page module runs.

``DUrl[str]`` accepts any non slash value.
Always validate the string before passing it into ORM lookups or external services.

.. code-block:: python
   :caption: notes/pages/notes/[slug]/page.py

   from django.shortcuts import get_object_or_404
   from notes.models import Note
   from next.pages import context
   from next.urls import DUrl

   @context("note")
   def note(slug: DUrl[str]) -> Note:
       return get_object_or_404(Note, slug=slug, is_public=True)

The ``is_public`` filter prevents an attacker from reading a private note by guessing the slug.

The same scoping rule applies to the ModelForm ``Meta.instance_from_url`` key.
Its default ``get_initial`` runs an unscoped ``get_object_or_404(model, slug=<value>)``, so a form that edits a per-user row must override ``get_initial`` with a scoped query such as ``get_object_or_404(Note, slug=slug, owner=request.user)``.
See :doc:`/content/topics/forms/modelforms` for the full pattern.

URL Kwargs on a POST
--------------------

A form POST targets the dispatch endpoint, not the page URL, so the page's captured kwargs are not in the request path.
The dispatcher recovers them by resolving the hidden ``_next_form_origin`` field against the URLconf, which runs the real URL converters and feeds the typed kwargs into ``get_initial`` and into DI-resolved ``on_valid`` parameters exactly like a live URL capture.

The origin field is user-controlled.
A client can replace it with the path of any other routed page before posting, so the recovered kwargs carry the same trust level as the path itself, not the higher trust of a server-set value.
They are the mechanism behind the ``instance_from_url`` insecure direct object reference, because the value that selects the row to edit travels in the request body.

Validate or scope every lookup that reads a URL kwarg, whether the value arrived in the live path or through the posted origin.

Query Strings
-------------

``DQuery[T]`` coerces the value to ``T`` for the same scalar set as ``DUrl``.
A value that fails to parse falls back to the raw query string rather than raising, so a crafted input cannot crash the page module.
``DQuery[str]`` returns the raw string.
``DQuery[list[T]]`` returns a list of coerced values.

Validate the resulting values against business rules.

.. code-block:: python
   :caption: notes/pages/page.py

   from next.pages import context
   from next.urls import DQuery

   @context("page")
   def page_number(page: DQuery[int] = 1) -> int:
       return max(1, min(page, 1000))

The example clamps the page number to a reasonable range to prevent denial of service through huge offsets.

Form Bodies
-----------

Form actions validate through Django form ``clean`` methods.
``form.is_valid()`` runs every validator before the handler sees the data.

Two rules apply.

Do not bypass validation.
   Always read ``form.cleaned_data`` inside the handler, never ``request.POST``.

Whitelist fields.
   Use ``Meta.fields`` to declare every editable field.
   Avoid ``Meta.exclude`` because new fields default to editable.

The ``cleaned_data`` rule covers the form fields, not the extra parameters DI injects into ``on_valid``.
A ``DUrl`` parameter or a URL kwarg argument on ``on_valid`` originates from the same untrusted path and posted origin described above, so validate or scope it the same way before using it in a lookup.

Custom Validators
-----------------

Add a custom validator when the type alone is not enough.

.. code-block:: python
   :caption: notes/validators.py

   from django.core.exceptions import ValidationError

   def positive_only(value: int) -> None:
       if value <= 0:
           raise ValidationError("Value must be positive.")

Apply the validator on the form field or on a custom DI provider.

Custom Providers
----------------

A custom provider that reads from external state (database, cache, HTTP) must validate inputs before the lookup.
The ``url_kwargs`` dict and ``request.GET`` are both untrusted.

.. code-block:: python
   :caption: notes/providers.py

   import re
   from typing import get_origin
   from django.http import Http404
   from next.deps import DDependencyBase, RegisteredParameterProvider
   from notes.models import Link

   class DLink[T](DDependencyBase[T]):
       __slots__ = ()

   class LinkProvider(RegisteredParameterProvider):
       def can_handle(self, param, _context) -> bool:
           return get_origin(param.annotation) is DLink

       def resolve(self, param, context):
           slug = str(context.url_kwargs["slug"])
           if not re.fullmatch(r"[a-zA-Z0-9-]{1,50}", slug):
               raise Http404
           try:
               return Link.objects.get(slug=slug)
           except Link.DoesNotExist:
               raise Http404 from None

The explicit length and character check make the validation visible to readers and to security audits.
The resolver does not wrap or swallow provider exceptions, so ``Http404`` raised here propagates unchanged through to Django's view layer.

Redirects
---------

Validate destination URLs before passing them to ``HttpResponseRedirect``.

.. code-block:: python
   :caption: notes/actions.py

   from django.http import HttpRequest, HttpResponseRedirect
   from django.urls import resolve, Resolver404
   from next.forms import Form, CharField
   from next.urls import DQuery

   class LoginForm(Form):
       username = CharField()
       password = CharField()

       def on_valid(self, request: HttpRequest, next_url: DQuery[str] = "/") -> HttpResponseRedirect:
           try:
               resolve(next_url)
           except Resolver404:
               next_url = "/"
           return HttpResponseRedirect(next_url)

The ``resolve`` call rejects external URLs and unknown paths.

Logging
-------

Log every dispatched action through ``action_dispatched``.
Log every failed validation through ``form_validation_failed``.
Logs make it possible to spot mass scraping, credential stuffing, and other automated abuse.

See Also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for the markers and providers.
   :doc:`/content/topics/forms/validation-rerender` for the validation flow.
   :doc:`overview` for the broader security picture.
