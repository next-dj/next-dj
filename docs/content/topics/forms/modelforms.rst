.. _topics-forms-modelforms:

ModelForms
==========

A :doc:`ModelForm <django:topics/forms/modelforms>` adapts a Django model to a form.
next.dj supports ModelForms anywhere a plain ``Form`` works.
This page covers the ``next.forms.ModelForm`` base class, the declarative ``Meta.instance_from_url`` key that loads an instance for edit pages, and the create-and-edit-with-one-class pattern that follows from it.

.. contents::
   :local:
   :depth: 2

Base Class Setup
----------------

Subclass the framework's ``ModelForm`` base class.
The class is the unit of registration.
Declaring it is enough to make it reachable by name from any template, and the action name is the ``snake_case`` of the class name.

.. code-block:: python
   :caption: notes/pages/notes/edit/[slug]/page.py — auto-registered as ``note_edit_form``

   import next.forms
   from notes.models import Note

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["slug", "title", "body"]
           instance_from_url = "slug"

There is no ``@action`` decorator on the class and no ``form_class`` argument anywhere.
Subclassing ``next.forms.ModelForm`` registers the form through the ``__init_subclass__`` hook the moment Python runs the ``class`` statement.
See :doc:`actions` for the full registration rules and scope derivation.

Loading an Instance From the URL
--------------------------------

``Meta.instance_from_url`` tells the default ``get_initial`` how to find the model instance an edit page operates on.
It is the primary CRUD path and replaces the hand-written instance lookup that older code carried in both ``get_initial`` and the handler.

String Form
~~~~~~~~~~~

A string names a URL kwarg that doubles as the model lookup field.

.. code-block:: python
   :caption: notes/pages/notes/edit/[slug]/page.py

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["slug", "title", "body"]
           instance_from_url = "slug"

On a page whose route captures ``slug``, the default ``get_initial`` runs :func:`~django.shortcuts.get_object_or_404` with ``Note.objects.get(slug=<captured value>)`` and binds the result as the form instance.

Dict Form
~~~~~~~~~

A dict maps a URL kwarg name to a different model lookup field for the case where the two names differ.

.. code-block:: python
   :caption: notes/pages/notes/edit/[int:id]/page.py

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["slug", "title", "body"]
           instance_from_url = {"id": "pk"}

Here the route captures ``id`` and the form loads the instance with ``Note.objects.get(pk=<captured value>)``.
The dict key is the URL kwarg name and the value is the model field used in the lookup.

Lookup Behaviour
~~~~~~~~~~~~~~~~

The declarative key has two boundary behaviours worth stating plainly.

A missing URL kwarg yields an unbound form.
On a create page with no ``[slug]`` segment, the captured kwarg is absent, ``get_initial`` returns an empty dict, and the form renders fresh.

A not-found object yields a 404.
When the kwarg is present but no row matches, :func:`~django.shortcuts.get_object_or_404` raises ``Http404`` and the request returns the standard 404 response.

The field named by ``instance_from_url`` should be unique.
:func:`~django.shortcuts.get_object_or_404` turns only the not-found case into a 404, so a lookup matching several rows surfaces as a server error instead.

Before and After
----------------

The wiki example carried an ``ArticleEditForm`` that loaded the article twice and mapped each field by hand.

.. code-block:: python
   :caption: before — manual lookup duplicated across get_initial and on_valid

   class ArticleEditForm(next.forms.Form):
       article_id = next.forms.IntegerField(widget=next.forms.HiddenInput)
       slug = next.forms.CharField()
       title = next.forms.CharField()
       body_md = next.forms.CharField(required=False)

       @classmethod
       def get_initial(cls, request, slug=None):
           if slug is None:
               return {}
           article = get_object_or_404(Article, slug=slug)
           return {
               "article_id": article.pk,
               "slug": article.slug,
               "title": article.title,
               "body_md": article.body_md,
           }

       def on_valid(self, request):
           article = get_object_or_404(Article, pk=self.cleaned_data["article_id"])
           article.slug = self.cleaned_data["slug"]
           article.title = self.cleaned_data["title"]
           article.body_md = self.cleaned_data.get("body_md", "")
           article.save()
           return redirect_to_origin(request)

The hidden ``article_id`` field, the second :func:`~django.shortcuts.get_object_or_404`, and the field-by-field assignment all exist only to relocate the instance the page already addressed in its URL.

.. code-block:: python
   :caption: after — instance loading is a single declarative line

   class ArticleEditForm(next.forms.ModelForm):
       class Meta:
           model = Article
           fields = ["slug", "title", "body_md"]
           instance_from_url = "slug"

The default ``get_initial`` loads the article and the default ``on_valid`` saves it.
The instance-loading plumbing collapses to one line.
The hidden ``article_id`` field, the second :func:`~django.shortcuts.get_object_or_404`, and the field-by-field copy all give way to ``instance_from_url = "slug"``.
A real ModelForm still carries whatever genuine logic the page needs, such as a custom ``on_valid``, a ``clean_slug`` validator, or widget overrides under ``Meta.widgets``.

Create and Edit With One Class
------------------------------

The same ModelForm class can drive both a create page and an edit page.
The route shape decides which mode the form runs in.

On a create page the route has no captured kwarg, so the form renders unbound and ``self.save()`` inserts a new row.

On an edit page the route captures the kwarg named by ``instance_from_url``, so the form loads the existing row and ``self.save()`` updates it.

.. code-block:: python
   :caption: notes/pages/notes/new/page.py — create, no captured kwarg

   import next.forms
   from notes.models import Note

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["slug", "title", "body"]
           instance_from_url = "slug"

The class above behaves as a create form on ``notes/new/`` and as an edit form on ``notes/edit/[slug]/``, with no per-page branching.
Auto-registration keys on the ``snake_case`` of the class name, so two pages that share one class share one action name and one action URL.

Separate create and edit forms are just as common, and the examples take that route.
The wiki create page declares a plain ``ArticleCreateForm`` while the edit page keeps the ``ArticleEditForm`` ModelForm.
The multi-tenant create page likewise declares its own ``NoteCreateForm`` apart from the edit form.
Split the two when the create and edit fields diverge, when validation differs, or when the rows must be scoped to something the URL does not carry.

Handling Submissions
--------------------

The default ``on_valid`` on ``ModelForm`` calls ``self.save()`` and then redirects to the origin page.
Override it only when the redirect target differs or extra logic must run after saving.

.. code-block:: python

   from django.http import HttpRequest, HttpResponseRedirect

   def on_valid(self, request: HttpRequest):
       article = self.save()
       return HttpResponseRedirect(article.url)

To attach a request-derived value before writing the row, save with ``commit=False`` first.

.. code-block:: python

   def on_valid(self, request: HttpRequest):
       note = self.save(commit=False)
       note.modified_by = request.user
       note.save()
       return redirect_to_origin(request)

Call ``self.save_m2m()`` after ``note.save()`` when the model has many-to-many fields.

Custom get_initial Escape Hatch
-------------------------------

Define ``get_initial`` only when the lookup the page needs cannot be expressed by ``instance_from_url``, for example a tenant-scoped query.

.. code-block:: python

   from django.http import HttpRequest
   from django.shortcuts import get_object_or_404
   from notes.access import get_active_tenant

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title", "body"]

       @classmethod
       def get_initial(cls, request: HttpRequest, id: int | None = None):
           tenant = get_active_tenant(request)
           if tenant is None or id is None:
               return {}
           return get_object_or_404(Note, pk=id, tenant=tenant)

The ``get_active_tenant`` helper reads the tenant the middleware attached to the request, so the lookup stays scoped to the active tenant.
Declare ``get_initial`` as a ``classmethod`` with a DI-friendly signature.
The framework resolves ``request`` and captured URL parameters as keyword arguments.
Return a model instance to bind an existing row for editing, or a dict to seed an unbound form.

Validation Failure
------------------

A failing validation re-renders the origin page with the bound form in scope.
For a ModelForm the user sees the values they typed plus any field errors.

The framework does not call ``save()`` when validation fails.
``on_valid`` runs only after ``self.is_valid()`` returns ``True``.
See :doc:`validation-rerender` for the re-render pipeline.

System Checks
-------------

Two checks guard ``Meta.instance_from_url``.
Both run statically when the class is declared, so ``uv run python manage.py check`` surfaces them before a request ever arrives.

``next.E048`` fires when ``instance_from_url`` names a field that does not exist on the model.
Correct the field name, or use the dict form to map the URL kwarg to the real model field.

``next.E049`` fires when ``instance_from_url`` is set on a class that is not a ModelForm.
The key only loads model instances, so move the declaration onto a ``next.forms.ModelForm`` subclass.

See Also
--------

.. seealso::

   :doc:`actions` for auto-registration, name derivation, and scope.
   :doc:`templates` for the ``{% form %}`` tag.
   :doc:`formsets` for collections of model instances.
   :doc:`/content/howto/use-modelform-for-crud` for a step-by-step recipe.
   :doc:`/content/ref/forms` for the public API.
   :doc:`Django ModelForm <django:topics/forms/modelforms>` and :func:`~django.shortcuts.get_object_or_404` for the underlying Django behaviour.
