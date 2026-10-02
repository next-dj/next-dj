.. _topics-seo-structured-data:

Structured data
===============

``jsonld`` carries the schema.org description of a page, as plain mappings or as nodes of ``next.pages.ld``.
Every node the page and its ancestors declare renders into one JSON-LD ``@graph``, so the Organization of ``DEFAULTS`` and the Product of a landing page describe one site together.
This page covers plain mappings, the ``ld`` nodes, the ``@id`` rules, and the graph.

.. contents::
   :local:
   :depth: 2

Plain mappings
--------------

A node is a mapping in the JSON-LD shape, ``@type`` and ``@id`` included.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "SITE": {"URL": "https://acme.example", "NAME": "Acme"},
       "METADATA": {
           "DEFAULTS": {
               "jsonld": [
                   {
                       "@type": "Organization",
                       "@id": "#org",
                       "name": "Acme",
                       "url": "https://acme.example/",
                       "logo": "https://acme.example/static/site/logo.png",
                   },
                   {"@type": "WebSite", "@id": "#website", "name": "Acme", "publisher": {"@id": "#org"}},
               ],
           },
       },
   }

A schema.org ``@context`` on a mapping is dropped, since the graph carries one, and a mapping with a foreign ``@context`` renders as its own ``<script>`` beside the graph.
Every ``@id`` in a schema.org mapping, nested ones included, resolves against the site as `@id and URLs`_ describes, while every other value renders as written.
A mapping under a foreign ``@context`` keeps every ``@id`` as written, and a ``Node`` or ``Ref`` inside it renders in place with its own ``@id`` unresolved.
Every value is checked when the metadata loads and written as plain dicts and lists when the page renders, so a ``MappingProxyType`` or a tuple serialises.
A value JSON cannot hold in a ``metadata`` dict, a ``set``, ``nan``, or a time with a time zone among them, is a ``PageMetadataShapeError``.
A :class:`~datetime.datetime`, a :class:`~datetime.date`, a :class:`~decimal.Decimal`, a UUID, and an enum member pass, the enum written as its value.
A naive datetime takes the current time zone as it renders, so the page and ``manage.py check`` agree on it.

The ld nodes
------------

``next.pages.ld`` holds the base of a typed node and the breadcrumb list the graph gains on its own.

.. list-table::
   :header-rows: 1
   :widths: 26 74

   * - Name
     - What it is
   * - ``Node``
     - A frozen base with ``id``, ``type``, and ``extra``, whose subclasses spell their properties in snake case and render them in camel case.
   * - ``Ref``
     - ``id`` alone, rendered as ``{"@id": ...}`` to point at a node declared elsewhere.
   * - ``BreadcrumbList``
     - ``items``, a tuple of ``ListItem(name=..., position=..., item=...)``.

A subclass of ``Node`` types the node a site repeats, so a missing required property is a ``TypeError`` where the node is built, at import for a module-level node, and a type error under mypy.
``TYPE`` names the schema.org type, and ``URLS`` the fields resolved the way an ``@id`` is.
``KEYS`` maps a field to the property it renders under when the camel case of its name is not the schema.org spelling, as ``BreadcrumbList`` renders ``items`` under ``itemListElement``.
``None`` and an empty tuple are left out, a nested node or a ``Ref`` renders in place, and ``extra`` adds verbatim properties last.

.. code-block:: python
   :caption: shop/ld.py

   from dataclasses import dataclass
   from decimal import Decimal
   from typing import ClassVar

   from next.pages import ld

   @dataclass(frozen=True, slots=True, kw_only=True)
   class Product(ld.Node):
       TYPE: ClassVar[str] = "Product"
       URLS: ClassVar[frozenset[str]] = frozenset({"image"})

       name: str
       image: tuple[str, ...] = ()
       brand: ld.Ref | None = None
       price: Decimal | None = None

.. code-block:: python
   :caption: shop/pages/products/[slug]/page.py

   from shop.ld import Product
   from shop.models import Item

   from next import page
   from next.pages import MetadataDict, ld

   @page.metadata
   def product_metadata(item: Item) -> MetadataDict:
       return {
           "title": item.name,
           "jsonld": [Product(id=f"/products/{item.slug}/#product", name=item.name, image=(item.photo.url,), brand=ld.Ref("#org"))],
       }

The Product renders ``{"@type": "Product", "@id": "https://acme.example/products/rocket/#product", "name": ..., "image": [...], "brand": {"@id": "https://acme.example/#org"}}``.

``@id`` and URLs
----------------

An ``@id`` that is a bare fragment such as ``#org`` resolves against the root of the site URL, ``https://acme.example/#org``, so one id names one node on every page.
Any other ``@id``, and every field a ``Node`` subclass lists in ``URLS``, resolves through the same ``absolute_url`` the canonical link uses.
A ``Ref("#org")`` or a plain ``{"@id": "#org"}`` therefore points at the Organization ``DEFAULTS`` declared, from any page.
``#org`` and ``/#org`` name the same node, so the merge by ``@id`` and the checks treat them as one.

The graph
---------

Every node renders into a single ``<script type="application/ld+json">`` carrying ``{"@context": "https://schema.org", "@graph": [...]}``.
The JSON is written by :class:`~django.core.serializers.json.DjangoJSONEncoder` with ``allow_nan=False``, and ``<``, ``>``, and ``&`` are escaped so a value can never close the script.
A node a ``@page.metadata`` callable builds with a value JSON cannot hold is left out of the graph and logged once, and under ``DEBUG`` the render raises.

``jsonld`` merges by ``@id`` along the tree.
A node whose raw ``@id`` an ancestor declared replaces that node in place, and a node without an ``@id`` appends.
``Replace([...])`` takes the list whole and ``RESET`` drops the inherited graph, see :doc:`merge`.
``manage.py check`` reports a node that does not serialise to JSON (``next.E127``), two nodes with one ``@id`` in one ``page.py`` (``next.W103``), and one ``@id`` declared under two types (``next.E101``).
The serialisation check writes the node through the same call the renderer makes, so a node that passes it renders.

See also
--------

.. seealso::

   :doc:`breadcrumbs` for the ``BreadcrumbList`` the graph gains on its own.
   :doc:`/content/ref/metadata` for the ``next.pages.ld`` API.
