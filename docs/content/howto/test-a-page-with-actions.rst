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

Set up pytest plus pytest-django and a session fixture that imports every ``page.py``, so the ``@action`` registrations are in place before the first request (see :doc:`/content/topics/testing`).

.. code-block:: python
   :caption: conftest.py

   from pathlib import Path

   import pytest
   from next.testing import eager_load_pages

   PROJECT_ROOT = Path(__file__).resolve().parent

   @pytest.fixture(autouse=True, scope="session")
   def _load_pages() -> None:
       eager_load_pages(PROJECT_ROOT / "notes" / "pages")

Write the test.

.. code-block:: python
   :caption: tests/test_create_flow.py

   from notes.models import Note
   from next.forms.signals import action_dispatched
   from next.testing.client import NextClient
   from next.testing.signals import SignalRecorder

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
