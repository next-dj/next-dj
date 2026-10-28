.. _howto-modelform-crud:

Use ModelForm for CRUD
======================

Problem
-------

You want create and edit pages for a model with as little glue code as possible, and every user may edit only the rows they own.

Solution
--------

Declare a ``next.forms.ModelForm`` for each page in a shared module such as ``notes/forms.py``.
The edit form loads its row through ``DObject`` in ``get_form_kwargs``, which reads it through ``visible_to(scope)``, and the create form hands the owner to the new row from the same hook.
No factory, no hidden lookup field, and no ownership check in ``on_valid`` are needed.

Walkthrough
-----------

Declare who sees which row
~~~~~~~~~~~~~~~~~~~~~~~~~~

``visible_to`` on the default manager answers which rows a scope may see, and the ``scope`` dependency decides what the scope is.
Here the scope is the signed-in user.

.. code-block:: python
   :caption: notes/models.py

   from django.conf import settings
   from django.db import models

   class NoteQuerySet(models.QuerySet):
       def visible_to(self, scope):
           if not scope.is_authenticated:
               return self.none()
           return self.filter(owner=scope)

   class Note(models.Model):
       owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
       title = models.CharField(max_length=200)
       body = models.TextField(blank=True)

       objects = NoteQuerySet.as_manager()

.. code-block:: python
   :caption: notes/deps.py

   from django.http import HttpRequest

   from next.deps import resolver

   @resolver.dependency("scope")
   def scope(request: HttpRequest):
       return request.user

The ``next`` application imports ``notes/deps.py`` at startup, so the dependency is registered before the first request.
See :ref:`topics-di-dobject` for how ``DObject`` reads the scope.

Declare the forms
~~~~~~~~~~~~~~~~~

Both classes live outside ``page.py``, so they register with shared scope and autodiscovery imports them on startup.

.. code-block:: python
   :caption: notes/forms.py

   from typing import Any

   from notes.models import Note

   import next.forms
   from next import Depends
   from next.deps import DObject

   class NoteCreateForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title", "body"]
           login_required = True

       @classmethod
       def get_form_kwargs(cls, scope=Depends("scope")) -> dict[str, Any]:
           return {"instance": Note(owner=scope)}

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title", "body"]
           login_required = True

       @classmethod
       def get_form_kwargs(cls, note: DObject[Note]) -> dict[str, Any]:
           return {"instance": note}

``get_form_kwargs`` returns the keyword arguments of the form constructor, and the dispatcher adds the posted data and files itself.
The default ``on_valid`` calls ``self.save()`` and redirects to the origin page, so the create form inserts a row that already carries its owner and the edit form updates the row it loaded.
``Meta.success_url`` redirects the successful submission to another page instead, and ``Meta.success_message`` flashes a message, see :ref:`topics-forms-actions-success`.
See :ref:`topics-forms-actions-form-kwargs` for the hook contract.

``Note(owner=scope)`` accepts only a signed-in user, so the pages that render these forms sit behind a login, see :doc:`require-login-on-pages`.
``Meta.login_required`` guards the action endpoint, which lies outside the page URL space.

Create page
~~~~~~~~~~~

.. code-block:: jinja
   :caption: notes/pages/notes/new/template.djx

   {% form "note_create_form" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Create</button>
   {% endform %}

Edit page
~~~~~~~~~

The edit page lives under a route that captures the primary key under the name of the ``DObject`` parameter, here ``note``.

.. code-block:: jinja
   :caption: notes/pages/notes/[int:note]/edit/template.djx

   {% form "note_edit_form" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

The ``{% form %}`` tag emits a hidden ``_next_form_origin`` field with the page URL, so the dispatcher recovers the captured ``note`` on the POST and ``DObject`` loads the same row again through ``visible_to``.
A note of another user answers 404 on the GET render and on the POST alike, since ``visible_to`` never returns it.

URL names follow the ``page_{name}`` convention where path segments are joined with underscores and captured-parameter brackets are dropped.
See :doc:`/content/topics/file-router` for the full naming rules.

Verification
------------

A test walks the flow with ``NextClient``.

.. code-block:: python
   :caption: tests/test_crud.py

   from django.contrib.auth.models import User
   from notes.models import Note

   from next.testing.client import NextClient

   def test_crud_flow(db) -> None:
       owner = User.objects.create_user("owner")
       client = NextClient()
       client.force_login(owner)
       client.post_action(
           "note_create_form", {"title": "Intro", "body": ""}, origin="/notes/new/"
       )
       note = Note.objects.get(owner=owner)
       client.post_action(
           "note_edit_form",
           {"title": "Intro v2", "body": ""},
           origin=f"/notes/{note.pk}/edit/",
       )
       assert Note.objects.get(pk=note.pk).title == "Intro v2"

   def test_foreign_note_is_not_found(db) -> None:
       note = Note.objects.create(owner=User.objects.create_user("owner"), title="Mine")
       client = NextClient()
       client.force_login(User.objects.create_user("intruder"))
       response = client.post_action(
           "note_edit_form",
           {"title": "Taken", "body": ""},
           origin=f"/notes/{note.pk}/edit/",
       )
       assert response.status_code == 404
       assert Note.objects.get(pk=note.pk).title == "Mine"

Each ``post_action`` passes ``origin``, which fills the ``_next_form_origin`` field the ``{% form %}`` tag emits.
The first names the create page and inserts a row owned by the signed-in user.
The second names the edit page, and resolving that path yields the ``note`` kwarg ``DObject`` reads.
The recovered kwargs pass through the URL converters of the resolved route, so ``[int:note]`` arrives as an integer.

See also
--------

.. seealso::

   :doc:`/content/topics/forms/modelforms` for the ModelForm topic guide.
   :doc:`/content/topics/dependency-injection` for ``DObject`` and the ``scope`` dependency.
   :doc:`/content/topics/forms/templates` for the ``{% form %}`` tag.
   :doc:`/content/topics/forms/validation-rerender` for the re-render flow.
