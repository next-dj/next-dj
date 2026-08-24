.. _howto-test-a-component-in-isolation:

Test a component in isolation
=============================

Problem
-------

You want pytest to render one component and assert on its HTML without standing up a full page, a route, or an HTTP request.

Solution
--------

Use ``render_component_by_name`` from ``next.testing``.
It resolves a component by name as seen from a given template path, renders it with a context mapping you supply, and returns the HTML string.

Walkthrough
-----------

Load the components
~~~~~~~~~~~~~~~~~~~

Component discovery is a side effect, so import the components before a test resolves one.
A session-scoped pytest fixture calls ``eager_load_components`` once.

.. code-block:: python
   :caption: conftest.py

   import pytest
   from next.testing import eager_load_components

   @pytest.fixture(autouse=True, scope="session")
   def _load_components() -> None:
       eager_load_components()

``eager_load_components`` covers the roots configured through ``COMPONENT_BACKENDS``.
Component folders inside a page tree register during the URL router walk instead, which runs when the URLconf first loads.
A suite whose other tests issue ``NextClient`` requests has already triggered the walk.
A suite that renders components without any HTTP triggers it by reversing one route in the same fixture, for example with ``page_reverse()`` from ``next.urls``.

Render the component
~~~~~~~~~~~~~~~~~~~~

``render_component_by_name`` takes the component name and the ``at`` path the component is referenced from.
The ``at`` path drives visibility, so pass a template inside the page tree that can see the component.
The ``context`` mapping fills the values the component template reads.

.. code-block:: python
   :caption: tests/test_info_card.py

   from next.testing import render_component_by_name

   def test_info_card_renders_title() -> None:
       html = render_component_by_name(
           "info_card",
           at="notes/pages/template.djx",
           context={"title": "Quick start", "subtitle": "Read this first"},
       )
       assert "Quick start" in html
       assert "info-card" in html

The helper raises ``LookupError`` when no visible component matches the name from the ``at`` path.

.. warning::

   The keys of the ``context`` mapping reach the render-time guard as the props of this call site, the way ``{% component "info_card" title="Quick start" %}`` would pass them.
   A component whose unkeyed ``@component.context`` returns one of those keys raises ``ValueError`` here too, so the isolated test fails wherever the page render would.

Assert on the markup
~~~~~~~~~~~~~~~~~~~~

The return value is a plain string, so any HTML assertion works.
The ``assert_has_class`` and ``find_anchor`` helpers from ``next.testing`` keep class and link checks readable.

.. code-block:: python
   :caption: tests/test_info_card.py

   from next.testing import assert_has_class, find_anchor, render_component_by_name

   def test_info_card_marks_the_root() -> None:
       html = render_component_by_name(
           "info_card",
           at="notes/pages/template.djx",
           context={"title": "Quick start"},
       )
       assert_has_class(html, "info-card")

   def test_info_card_links_to_detail() -> None:
       html = render_component_by_name(
           "info_card",
           at="notes/pages/template.djx",
           context={"title": "Quick start", "href": "/notes/1/"},
       )
       anchor = find_anchor(html, href="/notes/1/", text="Quick start")
       assert 'href="/notes/1/"' in anchor

Pass a request when the component needs one
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When a component callable reads the request, build one with :class:`~django.test.RequestFactory` and pass it through the ``request`` keyword.

.. code-block:: python
   :caption: tests/test_user_badge.py

   from django.test import RequestFactory
   from next.testing import render_component_by_name

   def test_user_badge_shows_username(db, django_user_model) -> None:
       user = django_user_model.objects.create_user("ada")
       request = RequestFactory().get("/")
       request.user = user
       html = render_component_by_name(
           "user_badge",
           at="notes/pages/template.djx",
           request=request,
       )
       assert "ada" in html

Verification
------------

Run the component tests.

.. code-block:: bash
   :caption: shell

   uv run pytest -k component

Every test passes.
Each component rendered on its own, with no page or route involved.

See also
--------

.. seealso::

   :doc:`/content/topics/testing` for the testing toolkit.
   :doc:`/content/howto/build-a-composite-component` for the component under test.
