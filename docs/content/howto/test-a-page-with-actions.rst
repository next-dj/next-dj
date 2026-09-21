.. _howto-test-actions:

Test a page with actions
========================

Problem
-------

You want pytest to drive a page that posts to a registered action, asserts the redirect, and verifies that a signal fired with the right payload.

Solution
--------

Use ``NextClient.post_action`` for the HTTP round trip and ``SignalRecorder`` to capture the signal payload.

Walkthrough
-----------

Set up pytest plus pytest-django, then load ``next.testing.plugin`` and point ``next_pages`` at the page root, so the ``@action`` registrations are in place before the first request (see :doc:`/content/topics/testing`).

.. code-block:: ini
   :caption: pytest.ini

   [pytest]
   DJANGO_SETTINGS_MODULE = config.settings
   pythonpath = .
   addopts = -p next.testing.plugin
   next_pages = notes/pages

Write the test.

.. code-block:: python
   :caption: tests/test_create_flow.py

   from notes.models import Note

   from next.forms.signals import action_dispatched
   from next.testing.capture import SignalRecorder
   from next.testing.client import NextClient

   def test_create_flow(db) -> None:
       client = NextClient()

       with SignalRecorder(action_dispatched) as recorder:
           response = client.post_action(
               "create_note",
               {"title": "First", "body": "Hello"},
           )

       assert response.status_code == 302
       assert response["Location"] == "/"

       event = recorder.last_for(action_dispatched)
       assert event.kwargs["action_name"] == "create_note"
       assert event.kwargs["form"].cleaned_data["title"] == "First"

       assert Note.objects.filter(title="First").exists()

``NextClient`` does not enforce CSRF by default, matching Django's test client, so the POST needs no token.

Test the failure path
~~~~~~~~~~~~~~~~~~~~~

A failing validation re-renders the origin page, so the test names the origin.
The ``origin`` keyword fills the ``_next_form_origin`` field the ``{% form %}`` tag emits in the browser.

.. code-block:: python
   :caption: tests/test_validation_failure.py

   from next.testing.client import NextClient

   def test_blank_title_rerenders(db) -> None:
       response = NextClient().post_action("create_note", {"title": ""}, origin="/")
       assert response.status_code == 200
       assert b"This field is required" in response.content

The status code is ``200`` because the dispatcher resolves the origin path and re-renders that page with the bound form in scope.

A protocol-level test asserts the rejection instead.
Without a resolvable origin the invalid branch cannot re-render and answers HTTP 400.

.. code-block:: python
   :caption: tests/test_validation_failure.py

   def test_blank_title_without_origin_is_rejected(db) -> None:
       response = NextClient().post_action("create_note", {"title": ""})
       assert response.status_code == 400

Test the guard, not the happy path
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A form action is unauthenticated until something guards it, and the dispatch endpoint lives outside the page URL space, so a middleware scoped to a URL prefix does not reach it.
A guard dropped in a refactor breaks no test that posts as a signed-in owner.
The test that catches the loss is the one that asserts a denial, and each layer answers with its own status, so assert the status the layer produces rather than a generic failure.

Take three guarded actions, one per layer.

.. code-block:: python
   :caption: notes/pages/notes/page.py

   from django.http import HttpRequest
   from notes.models import Note

   import next.forms

   class NoteCreateForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")
           login_required = True

   class NotePublishForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ("title",)
           instance_from_url = "slug"
           permission_required = "notes.publish_note"

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")
           instance_from_url = "slug"
           login_required = True

       def has_object_permission(self, request: HttpRequest) -> bool:
           return self.instance.owner_id == request.user.id

An anonymous POST to a login-guarded action redirects to ``LOGIN_URL`` with the posted origin in ``next``.
The static guard runs before the form is built, so no row is written and no application code runs.
The origin passes a same-site string check rather than a URLconf lookup, and a missing or off-site value produces ``next=/`` instead.

.. code-block:: python
   :caption: tests/test_guards.py

   from notes.models import Note

   from next.testing.client import NextClient

   def test_anonymous_create_redirects_to_login(db) -> None:
       response = NextClient().post_action(
           "note_create_form",
           {"title": "First", "body": "Hello"},
           origin="/notes/",
       )
       assert response.status_code == 302
       assert response["Location"] == "/accounts/login/?next=/notes/"
       assert not Note.objects.exists()

The ``Location`` prefix is whatever ``LOGIN_URL`` names, so a project that moves its login page asserts its own value there.

A signed-in user who lacks a declared permission raises :exc:`~django.core.exceptions.PermissionDenied`, which Django answers with HTTP 403.
Declaring ``permission_required`` implies authentication, so the same action answers ``302`` for an anonymous caller and ``403`` for a signed-in one without the permission.

.. code-block:: python
   :caption: tests/test_guards.py

   from django.contrib.auth import get_user_model
   from notes.models import Note

   from next.testing.client import NextClient

   def test_user_without_permission_is_forbidden(db) -> None:
       user = get_user_model().objects.create_user("editor")
       Note.objects.create(slug="intro", title="Intro", body="", owner=user)

       client = NextClient()
       client.force_login(user)
       response = client.post_action(
           "note_publish_form",
           {"title": "Intro"},
           origin="/notes/intro/",
       )
       assert response.status_code == 403

The object-level layer runs after the form binds, so it sees the loaded row.
A POST from a user who does not own that row is refused with HTTP 403 and the row is untouched.

.. code-block:: python
   :caption: tests/test_guards.py

   from django.contrib.auth import get_user_model
   from notes.models import Note

   from next.testing.client import NextClient

   def test_stranger_cannot_edit_another_users_note(db) -> None:
       users = get_user_model().objects
       owner = users.create_user("owner")
       stranger = users.create_user("stranger")
       note = Note.objects.create(slug="intro", title="Intro", body="", owner=owner)

       client = NextClient()
       client.force_login(stranger)
       response = client.post_action(
           "note_edit_form",
           {"title": "Hijacked", "body": ""},
           origin=f"/notes/edit/{note.slug}/",
       )
       assert response.status_code == 403
       assert Note.objects.get(pk=note.pk).title == "Intro"

The denial also wins over an invalid form, which is worth its own assertion.
A submission that both fails validation and fails the ownership check answers ``403`` rather than the ``200`` re-render of the failure path above, so a stranger never reads the origin page with another user's row bound into it.

.. code-block:: python
   :caption: tests/test_guards.py

   def test_stranger_is_denied_before_validation(db) -> None:
       users = get_user_model().objects
       owner = users.create_user("owner")
       stranger = users.create_user("stranger")
       note = Note.objects.create(slug="intro", title="Intro", body="", owner=owner)

       client = NextClient()
       client.force_login(stranger)
       response = client.post_action(
           "note_edit_form",
           {"title": ""},
           origin=f"/notes/edit/{note.slug}/",
       )
       assert response.status_code == 403

Pair every denial test with the matching success, the owner editing the row and the permitted user publishing it.
A denial test on its own passes for the wrong reason once the action stops guarding the right thing, and it cannot pass for the wrong reason through a name typo, because ``post_action`` raises ``FormActionNotFoundError`` for an action the registry does not hold.

Run the CSRF check in one test
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``NextClient`` subclasses Django's test client, so it takes the same ``enforce_csrf_checks`` flag and turns the middleware check back on for one client.
The dispatch endpoint is not exempt from that check, so a POST without a token answers HTTP 403 before the view runs.
Use an unguarded action for this test, so the 403 can come from nothing but the missing token.

.. code-block:: python
   :caption: tests/test_csrf.py

   from next.testing.client import NextClient

   def test_post_without_a_token_is_forbidden(db) -> None:
       client = NextClient(enforce_csrf_checks=True)
       response = client.post_action("create_note", {"title": "First", "body": "Hello"})
       assert response.status_code == 403

The complement asserts that a tag-rendered form carries what the middleware wants.
Read the page, lift the hidden fields out of the rendered form, and post them back through the same CSRF-enforcing client.
The GET sets the CSRF cookie that the token in those fields is checked against.

.. code-block:: python
   :caption: tests/test_csrf.py

   from next.testing.client import NextClient
   from next.testing.html import find_form, form_action, hidden_fields

   def test_rendered_form_posts_under_csrf_enforcement(db) -> None:
       client = NextClient(enforce_csrf_checks=True)
       page = client.get("/notes/")
       form = find_form(
           page.content.decode(), action=client.get_action_url("create_note")
       )
       response = client.post(
           form_action(form),
           data=hidden_fields(form) | {"title": "First", "body": "Hello"},
       )
       assert response.status_code == 302

The two tests together say that the protection is on and that the supported rendering path satisfies it.

Render the page without HTTP
----------------------------

For tests that focus on template output, render the page directly.

.. code-block:: python
   :caption: tests/test_template.py

   from pathlib import Path

   from next.testing.rendering import render_page

   def test_template_renders_form() -> None:
       page = Path(__file__).parent.parent / "notes" / "pages" / "page.py"
       html = render_page(page)
       assert "Create" in html

Verification
------------

Run the suite.

.. code-block:: bash
   :caption: shell

   uv run pytest -k notes

Every test passes.
The dispatch ran through the signal, the model row exists, the failure path stayed on the origin page.

See also
--------

.. seealso::

   :doc:`/content/topics/testing` for the testing toolkit.
   :doc:`/content/topics/forms/signals` for signal payloads.
   :ref:`topics-forms-actions-guards` for the guard contract behind the asserted statuses.
   :doc:`/content/howto/enforce-object-level-permissions` for the owner-only edit under test.
   :doc:`/content/security/csrf-and-forms` for the CSRF flow the enforcing client exercises.
