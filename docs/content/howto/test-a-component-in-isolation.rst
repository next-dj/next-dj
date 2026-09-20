.. _howto-test-a-component-in-isolation:

Test a component in isolation
=============================

Problem
-------

You want pytest to render one component and assert on its HTML without standing up a full page, a route, or an HTTP request.

Solution
--------

Use ``render_component_by_name`` from ``next.testing``.
It resolves a component by name as seen from a given template path, renders it with the mappings you supply, and returns the HTML string.

Walkthrough
-----------

Load the components
~~~~~~~~~~~~~~~~~~~

Component discovery is a side effect, so import the components before a test resolves one.
``next_components = true`` in ``pytest.ini`` calls ``eager_load_components`` once per session through ``next.testing.plugin``.
A component that lives beside a page rather than in a configured root needs it, because the live registry holds such a folder only once a request has walked its page tree.

.. code-block:: ini
   :caption: pytest.ini

   [pytest]
   DJANGO_SETTINGS_MODULE = config.settings
   pythonpath = .
   addopts = -p next.testing.plugin
   next_components = true

``eager_load_components`` covers the roots configured through ``COMPONENT_BACKENDS``.
Component folders inside a page tree register during the URL router walk instead, which runs when the URLconf first loads.
A suite whose other tests issue ``NextClient`` requests has already triggered the walk.
A suite that renders components without any HTTP triggers it by reversing one route in a session fixture, for example with ``page_reverse()`` from ``next.urls``.

Render the component
~~~~~~~~~~~~~~~~~~~~

``render_component_by_name`` takes the component name and the ``at`` path the component is referenced from.
The ``at`` path drives visibility, so pass a template inside the page tree that can see the component.
The ``props`` mapping stands in for a ``{% component %}`` call site, so it fills the values the component template reads.

.. code-block:: python
   :caption: tests/test_info_card.py

   from next.testing import render_component_by_name

   def test_info_card_renders_title() -> None:
       html = render_component_by_name(
           "info_card",
           at="notes/pages/template.djx",
           props={"title": "Quick start", "subtitle": "Read this first"},
       )
       assert "Quick start" in html
       assert "info-card" in html

The helper raises ``LookupError`` when no visible component matches the name from the ``at`` path.

The ``at`` path is resolved against the process working directory, so a relative string only holds while the suite runs from the project root.
A suite that may run from elsewhere builds the anchor from the test file.

.. code-block:: python
   :caption: tests/test_info_card.py

   from pathlib import Path

   TEMPLATE = Path(__file__).resolve().parents[1] / "notes" / "pages" / "template.djx"

A component that comes from a ``COMPONENT_BACKENDS`` ``DIRS`` root is visible from any anchor, so only components that live inside a page tree depend on the value.

.. warning::

   The keys of the ``props`` mapping reach the render-time guard as the props of this call site, the way ``{% component "info_card" title="Quick start" %}`` would pass them.
   A component whose unkeyed ``@component.context`` returns one of those keys raises ``ValueError`` here too, so the isolated test fails wherever the page render would.

Pass ambient page values through ``context``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A component that reads a value from the surrounding page scope rather than from its own call site takes it through the ``context`` mapping.
Those keys are not published as props, so an unkeyed ``@component.context`` may shadow them exactly as it does on a page.
The helper applies the mapping over the ambient keys it seeded from ``at``, so a ``context`` entry naming one of them replaces the seeded value.

.. code-block:: python
   :caption: tests/test_info_card.py

   def test_info_card_reads_the_page_scope() -> None:
       html = render_component_by_name(
           "info_card",
           at="notes/pages/template.djx",
           context={"section": "Guides"},
           props={"title": "Quick start"},
       )
       assert "Guides" in html

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
           props={"title": "Quick start"},
       )
       assert_has_class(html, "info-card")

   def test_info_card_links_to_detail() -> None:
       html = render_component_by_name(
           "info_card",
           at="notes/pages/template.djx",
           props={"title": "Quick start", "href": "/notes/1/"},
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

Collect the assets a nested component registers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A page render hands the component runtime its own collector, and an isolated render has none until the test supplies one.
Pass a ``StaticCollector`` through the ``collector`` keyword to catch the co-located assets of the component and of anything it nests.

.. code-block:: python
   :caption: tests/test_info_card.py

   from next.static import StaticCollector
   from next.testing import render_component_by_name

   def test_info_card_registers_its_stylesheet() -> None:
       collector = StaticCollector()
       render_component_by_name(
           "info_card",
           at="notes/pages/template.djx",
           props={"title": "Quick start"},
           collector=collector,
       )
       styles = collector.assets_in_slot("styles")
       assert any("info_card" in asset.url for asset in styles)

Pass ``page_module_path`` when the component body holds a page-scoped ``{% form %}`` or ``{% action_url %}``, naming the ``page.py`` the action resolves against.

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
