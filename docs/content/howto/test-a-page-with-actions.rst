.. _howto-test-actions:

Test a Page With Actions
========================

Problem
-------

You want pytest to drive a page that posts to a registered action, asserts the redirect, and verifies that a signal fired with the right payload.

Solution
--------

Use ``NextClient.post_action`` for the HTTP round trip and ``SignalRecorder`` to capture the signal payload.

Walkthrough
-----------

Set up pytest plus pytest-django and an isolation fixture (see :doc:`/content/topics/testing`).

.. code-block:: python
   :caption: conftest.py

   import pytest

   from next.testing.isolation import reset_registries


   @pytest.fixture(autouse=True)
   def _next_isolation():
       reset_registries()
       yield
       reset_registries()

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

       event = recorder.events[-1]
       assert event.kwargs["action_name"] == "create_note"
       assert event.kwargs["form"].cleaned_data["title"] == "First"

       assert Note.objects.filter(title="First").exists()

Test the Failure Path
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: tests/test_validation_failure.py

   from next.testing.client import NextClient


   def test_blank_title_rerenders(db) -> None:
       response = NextClient().post_action("create_note", {"title": ""})
       assert response.status_code == 200
       assert b"This field is required" in response.content

The status code is ``200`` because the dispatcher re-renders the origin page with the bound form in scope.

Render the Page Without HTTP
----------------------------

For tests that focus on template output, render the page directly.

.. code-block:: python
   :caption: tests/test_template.py

   from next.testing.rendering import render_page


   def test_template_renders_form() -> None:
       html = render_page("notes/routes/page.py")
       assert "Create" in html

Verification
------------

Run the suite.

.. code-block:: bash
   :caption: shell

   uv run pytest -k notes

Every test passes.
The dispatch ran through the signal, the model row exists, the failure path stayed on the origin page.

See Also
--------

.. seealso::

   :doc:`/content/topics/testing` for the testing toolkit.
   :doc:`/content/topics/forms/signals` for signal payloads.
