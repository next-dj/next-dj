.. _howto-read-query-parameters:

Read Query Parameters
=====================

Problem
-------

A listing page needs the search term, page number, and selected filters from the query string.
You want typed values without writing ``request.GET.get(...)`` plumbing in every callable.

Solution
--------

Annotate a ``@context`` parameter with the ``DQuery[T]`` marker.
The framework reads :attr:`request.GET <django:django.http.HttpRequest.GET>`, coerces the value to the annotated type, and injects it.
``DQuery`` supports ``str``, ``int``, ``bool``, ``float``, and ``list[T]`` for multi-value parameters.

Walkthrough
-----------

Read a Single Parameter
~~~~~~~~~~~~~~~~~~~~~~~~

Declare the parameter with a type and a default.
The default is used when the key is absent from the query string.

.. code-block:: python
   :caption: storefront/page.py

   from catalog.models import Product

   from next.pages import context
   from next.urls import DQuery


   DEFAULT_FEATURED = 3
   MAX_FEATURED = 12


   @context("featured")
   def featured(show: DQuery[int] = DEFAULT_FEATURED) -> list[Product]:
       count = max(1, min(MAX_FEATURED, show))
       return list(
           Product.objects.filter(in_stock=True)
           .select_related("category")
           .order_by("-created_at")[:count],
       )

A request to ``/?show=8`` injects ``show=8`` as an ``int``.
A request to ``/`` injects the default ``3``.
Clamp the value yourself, since the marker only coerces the type.

Type Coercion
~~~~~~~~~~~~~

The annotation drives coercion of the raw query string.

``DQuery[int]``.
   Parsed with ``int()``. A value that does not parse, such as ``?show=abc``, falls back to the raw string rather than raising.
   Validate when a bad value must be rejected.

``DQuery[float]``.
   Parsed with ``float()``, with the same string fallback on a parse failure.

``DQuery[bool]``.
   ``True`` when the value is ``1``, ``true``, or ``yes``, case-insensitive. Every other value, including ``0`` and ``false``, is ``False``.

``DQuery[str]``.
   The raw value, unchanged.

``DQuery[list[T]]``.
   The value is split across three wire formats in priority order.
   The plain repeated form ``?brand=Acme&brand=Globex`` wins first.
   The bracket-suffix form ``?brand[]=Acme&brand[]=Globex`` (emitted by axios and similar clients) is the second fallback.
   The comma-delimited form ``?brand=Acme,Globex`` is the third fallback.
   Each element is then coerced using the same rules as the scalar form for ``T``.

A parameter that is absent from the query string receives the declared default, or ``None`` when no default is given.

Read Several Typed Parameters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A single callable can mix scalar and list parameters.
Each annotation drives its own coercion.

.. code-block:: python
   :caption: storefront/catalog/page.py

   from next.pages import context
   from next.urls import DQuery


   @context("results")
   def results(
       q: DQuery[str] = "",
       page: DQuery[int] = 1,
       in_stock: DQuery[bool] = False,
       brand: DQuery[list[str]] = (),
   ) -> dict:
       ...

List elements follow the same three wire formats described above under *Type Coercion*.

Build a Typed Snapshot With a Provider
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When several callables need the same filter set, parse it once into a frozen dataclass.
``get_multi_values`` reads a multi-value parameter through the same three wire formats as ``DQuery[list[T]]``.

.. code-block:: python
   :caption: catalog/providers.py

   from dataclasses import dataclass

   from next.urls import get_multi_values


   @dataclass(frozen=True, slots=True)
   class Filters:
       q: str = ""
       brands: tuple[str, ...] = ()
       in_stock: bool = False
       sort: str = "newest"


   def parse_filters(request):
       g = request.GET
       return Filters(
           q=g.get("q", "").strip(),
           brands=tuple(get_multi_values(request, "brand")),
           in_stock=g.get("in_stock") in {"1", "true", "on"},
           sort=g.get("sort") or "newest",
       )

Render the Form
~~~~~~~~~~~~~~~~

Search is idempotent, so the filter form uses ``method="get"`` and posts back to the same page.
A bookmarked URL reproduces the same listing.
Reserve ``@action`` for POST side effects such as creating or deleting rows.

.. code-block:: jinja
   :caption: storefront/catalog/_cards/filter_panel/component.djx

   <form method="get" action="{{ submit_url }}" data-filter-form>
     <input name="q" type="search" value="{{ current_filters.q }}"/>
     {% for brand in all_brands %}
       <label>
         <input type="checkbox" name="brand" value="{{ brand }}"
                {% if brand in current_filters.brands %}checked{% endif %}/>
         {{ brand }}
       </label>
     {% endfor %}
     {% component "button" type="submit" text="Apply filters" variant="default" %}
   </form>

Share the Snapshot Across a Layout Chain
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Register a resolved value with ``inherit_context=True`` so nested pages receive the same instance through DI.
The ``[category]`` bracket segment becomes a URL kwarg.
The callable reads it with ``DUrl[str]`` through a parameter named ``category`` to match the segment, then resolves it once.
Child callables then ask for ``category`` by parameter name and never re-query.

.. code-block:: python
   :caption: storefront/catalog/[category]/page.py

   from catalog.models import Category
   from django.http import Http404

   from next.pages import context
   from next.urls import DUrl


   @context("category", inherit_context=True)
   def category(category: DUrl[str]) -> Category:
       try:
           return Category.objects.get(slug=category)
       except Category.DoesNotExist as exc:
           raise Http404 from exc

A descendant page reads the resolved instance back through its parameter name.

.. code-block:: python
   :caption: storefront/catalog/[category]/products/page.py

   from catalog.models import Category, Product

   from next.pages import context


   @context("products")
   def products(category: Category) -> list[Product]:
       return list(Product.objects.filter(category=category))

Verification
------------

Open the listing page with a faceted query string and confirm the response reflects every parameter.

.. code-block:: bash
   :caption: shell

   uv run python manage.py runserver

Visiting ``/catalog/?q=iphone&brand=Acme&brand=Globex&in_stock=1&page=2`` filters by search term, two brands, and stock, on the second page.
The bracket form ``?brand[]=Acme&brand[]=Globex`` and the comma form ``?brand=Acme,Globex`` produce the same listing.

See Also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for the built-in providers.
   :doc:`/content/howto/reverse-urls` for building query strings from code.
   :doc:`/content/topics/url-reversing` for ``DUrl`` on the path versus ``DQuery`` in the query string.
   :doc:`/content/topics/file-router` for bracket segments that become URL kwargs.
