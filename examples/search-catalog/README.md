# Search catalog

A faceted product catalog with search, brand filters, price range, in-stock
toggle, sort, and pagination. The listing renders six cards per page with
deduplicated co-located CSS, the category and product detail routes thread
an inherited `Category` instance through the `[category]/[slug]/` chain
without re-querying, and identical search requests share a single
`LocMemCache` entry through the lifetime of one process.

The example focuses on the file-router and DI subsystems of next-dj.
It dogfoods a new core `DQuery[T]` provider that mirrors `DUrl[T]` for
query-string parameters. Two domain providers, `DFilters` and `DPage`,
build typed snapshots from `request.GET` with `DFilters` reusing
`QueryParamProvider` for the brand list. A `cached_search` helper is
keyed by a stable hash of the filter set. An `active_filters` context
processor surfaces a chip strip with a precomputed drop URL per chip.
A three-level nested layout chain (`storefront` then `catalog` then
`[category]`) wires the rest of the page tree.

## What you will see

| URL | Description |
|-----|-------------|
| `/` | Landing. Three featured products plus a category grid. |
| `/catalog/` | All products with sidebar filters, chips, and pagination. |
| `/catalog/?brand=Acme&brand=Globex` | Plain repeated-key brand filter. |
| `/catalog/?brand[]=Acme&brand[]=Globex` | qs-style bracket-suffix filter for axios clients. |
| `/catalog/?brand=Acme,Globex` | Comma-delimited filter for compact URLs. |
| `/catalog/?q=iPhone&page=2` | Search and pagination preserved across query overrides. |
| `/catalog/electronics/` | Category-scoped listing. Inherited `Category` shows in the breadcrumb. |
| `/catalog/electronics/iphone-15/` | Product detail. The `Category` instance comes through inherited context, no extra query. |

The user flow:

```
/  →  /catalog/  →  apply filters  →  paginate  →  /catalog/<category>/  →  /catalog/<category>/<slug>/
```

## How to run

```bash
cd examples/search-catalog
uv run python manage.py migrate        # schema + demo data in one step
uv run python manage.py runserver      # http://127.0.0.1:8000/
uv run pytest                          # 50 tests, 100% coverage
```

Tailwind loads via the Play CDN in
[`catalog/storefront/layout.djx`](catalog/storefront/layout.djx). No
Node, no build step. The two composite components have co-located CSS
that the static collector picks up, deduplicates, and emits exactly
once per page.

## Project tour

```
examples/search-catalog/
├── config/
│   ├── settings.py             # PAGES_DIR="storefront", COMPONENTS_DIR="_cards"
│   │                           # context_processors=["catalog.context_processors.active_filters"]
│   └── urls.py                 # Only include('next.urls')
└── catalog/
    ├── apps.py                 # AppConfig.ready() imports providers
    ├── models.py               # Category, Product (with composite uniqueness on slug)
    ├── providers.py            # Filters, PageRequest, FiltersProvider, PageProvider, parse_filters
    ├── queries.py              # cached_search with a stable blake2b cache key
    ├── context_processors.py   # active_filters builds chip descriptors and a drop-filter map
    ├── templatetags/
    │   └── catalog_qs.py       # {% querystring %} tag and `kv` filter for chip URLs
    ├── migrations/
    │   ├── 0001_initial.py     # schema
    │   └── 0002_seed_catalog.py        # pre-loads the demo catalog
    └── storefront/             # ← PAGES_DIR
        ├── layout.djx          # Root chrome with Tailwind and the {% collect_styles %} sink
        ├── page.py             # @context("featured", "categories")
        ├── template.djx        # Landing UI
        ├── _cards/             # ← COMPONENTS_DIR scoped at storefront level
        │   ├── product_card/
        │   │   ├── component.py
        │   │   ├── component.djx
        │   │   └── component.css
        │   └── filter_panel/
        │       ├── component.py
        │       ├── component.djx
        │       └── component.css
        └── catalog/
            ├── layout.djx      # Two-column grid with filter sidebar and chip strip
            ├── page.py         # @context("page_obj", "all_categories", "all_brands")
            ├── template.djx
            └── [category]/
                ├── layout.djx  # Category banner and breadcrumb
                ├── page.py     # @context("category", inherit_context=True), page_obj, all_brands
                ├── template.djx
                └── [slug]/
                    ├── page.py # @context("product") receives the inherited Category
                    └── template.djx
```

## Walking the code

### 1. Why search is plain HTML, not `@action`

Search is idempotent. A bookmark of `?q=iphone&brand=Acme&page=2`
should reproduce the same listing. That is the natural shape of
`<form method="get">` posting back to the same page. `@action` is for
POST side effects such as creating, updating, or deleting rows.
[`catalog/storefront/_cards/filter_panel/component.djx`](catalog/storefront/_cards/filter_panel/component.djx)
renders a regular HTML form. The `submit_url` value comes from
[`catalog/storefront/_cards/filter_panel/component.py`](catalog/storefront/_cards/filter_panel/component.py)
which reverses the current category page when scoped, otherwise the
all-products listing. Pagination uses
[`catalog/templatetags/catalog_qs.py`](catalog/templatetags/catalog_qs.py)
to keep every other query parameter intact.

### 2. The new `DQuery[T]` provider

[`next/urls/markers.py`](../../next/urls/markers.py) ships a marker
that mirrors `DUrl[T]` and reads `request.GET`. Any future GET
listing or admin filter can declare a typed parameter and skip the
`request.GET.get(...)` plumbing.

```python
from next.pages import context
from next.urls import DQuery

@context("results")
def search(
    q: DQuery[str] = "",
    page: DQuery[int] = 1,
    brand: DQuery[list[str]] = (),
):
    ...
```

The list form accepts three wire formats. Plain repeated keys
`?brand=a&brand=b` produced by `<form method="get">` win first. The
qs-style bracket suffix `?brand[]=a&brand[]=b` emitted by axios is the
second fallback. The comma-delimited form `?brand=a,b` produced by
`qs.stringify` with the comma array format is the third fallback.
Empty segments around commas are dropped.

### 3. Domain providers built on top of the core

[`catalog/providers.py`](catalog/providers.py) wraps the raw query
string in two typed snapshots so handlers stay declarative.

```python
@dataclass(frozen=True, slots=True)
class Filters:
    q: str = ""
    brands: tuple[str, ...] = ()
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    in_stock: bool = False
    sort: str = "newest"

class DFilters(DDependencyBase["Filters"]): ...
class DPage(DDependencyBase["PageRequest"]): ...
```

`FiltersProvider.resolve` runs `parse_filters(request)` which is also
reused by the active-filter context processor. The brand list inside
`parse_filters` delegates to `QueryParamProvider`, so all three wire
formats supported by `DQuery` (plain repeated, bracket suffix,
comma-delimited) flow through one helper. `PageProvider.resolve`
returns a clamped `PageRequest` whose `per_page` is bounded by
`MAX_PER_PAGE = 60`.

The landing page exercises `DQuery` directly. The `featured`
callable in
[`catalog/storefront/page.py`](catalog/storefront/page.py) accepts an
optional `?show=N` parameter through `show: DQuery[int] = 3`, with no
manual `request.GET.get` plumbing.

### 4. Three-level nested layouts

The compose chain is automatic. When a listing is rendered the body
is substituted into the innermost layout that has a
`{% block template %}{% endblock %}` slot, then the next ancestor, and
so on. For `/catalog/electronics/iphone-15/` the chain is

```
catalog/storefront/catalog/[category]/[slug]/template.djx
  └─ catalog/storefront/catalog/[category]/layout.djx     # category banner
      └─ catalog/storefront/catalog/layout.djx            # filter sidebar + chip strip
          └─ catalog/storefront/layout.djx                # Tailwind chrome
```

Each layer contributes a meaningful piece of UI. None of the layouts
are decorative wrappers added "in case we need them later".

### 5. `inherit_context=True` in a real flow

[`catalog/storefront/catalog/[category]/page.py`](catalog/storefront/catalog/%5Bcategory%5D/page.py)
registers `category` as inherit-context. Three downstream callables
on the same page (`page_obj`, `all_brands`, plus the breadcrumb on the
template) receive the same `Category` instance through DI.

```python
@context("category", inherit_context=True)
def category(category: object) -> Category:
    if isinstance(category, Category):
        return category
    try:
        return Category.objects.get(slug=category)
    except Category.DoesNotExist as exc:
        raise Http404 from exc
```

The parameter is annotated `object` because
`_collect_inherited_context` evaluates the callable twice when the
page itself is being rendered (once for the URL slug, once for the
already-resolved instance from `context_data`). The
`isinstance(category, Category)` short-circuit covers the second pass.

The product detail page in
[`catalog/storefront/catalog/[category]/[slug]/page.py`](catalog/storefront/catalog/%5Bcategory%5D/%5Bslug%5D/page.py)
declares `category: Category` and gets the inherited instance through
[`next/pages/context.py`](../../next/pages/context.py)'s
`ContextByDefaultProvider`. No re-query, no kwargs threading, no
helper.

### 6. Co-located CSS and dedup

`product_card` is rendered six times on the catalog listing and three
more times on the landing page. Its
[`component.css`](catalog/storefront/_cards/product_card/component.css)
emits `transition` and `:hover` rules. The static collector emits
exactly one `<link rel="stylesheet">` per asset URL across both
pages. The
[`tests/test_e2e.py::TestRouting::test_product_card_css_dedup`](tests/test_e2e.py)
and
[`test_landing_reuses_product_card_css`](tests/test_e2e.py)
tests assert this directly. The component lives at
[`storefront/_cards/`](catalog/storefront/_cards/) one level above
the catalog tree because both the landing and the listing render it.

### 7. `cached_search` and the LocMem hit path

[`catalog/queries.py`](catalog/queries.py) materialises the page slice
into a list so the cached payload does not depend on a queryset
closure that can grow stale across requests. The cache key is a
stable blake2b hash of a sorted JSON encoding of the filter set, page
number, page size, and category PK. Two identical GETs produce one
cache key and serve the second request from memory, which the
[`tests/test_e2e.py::TestCacheHit`](tests/test_e2e.py) tests verify
through the cache backend's internal map.

### 8. Active filter chips

[`catalog/context_processors.py`](catalog/context_processors.py)
returns a `chips` list and a `drop_filter_qs` map keyed by
`"key=value"`. The map stores the query string with that one pair
removed, which the layout consumes through the `kv` template filter.
Clicking a chip drops one filter at a time without disturbing the
rest.

The default `sort=newest` is filtered out of the chip list to avoid
permanent noise.

## Tests

- [`tests/test_e2e.py`](tests/test_e2e.py) covers routing, filtering,
  the chip strip, the inherit-context pathway, and the cache. The
  filter test is parametrised across all three wire formats so plain
  repeated, bracket-suffix, and comma-delimited queries all run.
- [`tests/test_unit.py`](tests/test_unit.py) covers the data migration
  (forward and reverse), model `__str__` methods, the `Filters` dataclass,
  every branch of `parse_filters`, the `PageProvider` clamping, and the
  `querystring` template helper.
- [`conftest.py`](conftest.py) ships three fixtures. `_load_pages`
  loads the storefront tree once per session through `eager_load_pages`
  from `next.testing`. `_isolate` clears the LocMem cache between
  tests. `catalog_db` gates tests on the pre-loaded demo catalog.

Total: 47 tests, 100% coverage on the `catalog` package.

## Forward-compat

| Future feature | Hook in this example |
|----------------|----------------------|
| Suspense (async heavy chunks) | `cached_search` is the single point of heavy render. Wrap with the future async-context decorator without touching templates. |
| Partial rerender of forms | Not applicable here. Search is GET-based and the filter form already round-trips through the URL. |
| Parent-policies (native inherit) | `@context("category", inherit_context=True)` already declares the intent. The native replacement is a one-line edit per `@context`. |
| React bridge | `product_card` exposes `data-product-card` and `data-product-slug` attributes. A future `component.jsx` next to `component.djx` gets bootstrapped through `@component.context("product", serialize=True)`. |

## Further reading

- [`next/urls/markers.py`](../../next/urls/markers.py) ships both the
  `DUrl` provider for URL path segments and the new `DQuery` provider
  for query-string parameters. The narrative section lives in
  [`docs/content/guide/dependency-injection.rst`](../../docs/content/guide/dependency-injection.rst#built-in-providers).
- [`next/pages/registry.py`](../../next/pages/registry.py) hosts the
  `_collect_inherited_context` walk used by `inherit_context=True`.
- [`next/pages/context.py`](../../next/pages/context.py) hosts the
  `ContextByDefaultProvider` that injects inherited values into
  downstream callables by parameter name.
- [`next/static/collector.py`](../../next/static/collector.py) hosts
  the static collector that deduplicates co-located component CSS.
