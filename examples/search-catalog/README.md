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
uv run pytest
```

Tailwind loads via the Play CDN in
[`catalog/storefront/layout.djx`](catalog/storefront/layout.djx). No
Node, no build step. Components carry co-located CSS and JS that the
static collector picks up, deduplicates, and emits exactly once per
page. The `filter_panel` component ships a small `component.js` that
auto-submits the form when any checkbox or dropdown changes and runs
live constraint validation on the search field (minimum 3 characters)
through the native Constraint Validation API, with a help text that
narrates exactly how many more characters are needed.

## Walking the code

### 1. Why search is plain HTML, not `@action`

Search is idempotent. A bookmark of `?q=iphone&brand=Acme&page=2`
should reproduce the same listing. That is the natural shape of
`<form method="get">` posting back to the same page. `@action` is for
POST side effects such as creating, updating, or deleting rows.
[`catalog/storefront/catalog/_cards/filter_panel/component.djx`](catalog/storefront/catalog/_cards/filter_panel/component.djx)
renders a regular HTML form. The `submit_url` value comes from
[`catalog/storefront/catalog/_cards/filter_panel/component.py`](catalog/storefront/catalog/_cards/filter_panel/component.py)
which reverses the current category page when scoped, otherwise the
all-products listing. Pagination uses
[`catalog/templatetags/catalog_qs.py`](catalog/templatetags/catalog_qs.py)
to keep every other query parameter intact.

### 2. The new `DQuery[T]` provider

[`next/urls/markers.py`](../../next/urls/markers.py) ships a marker
that mirrors `DUrl[T]` and reads `request.GET`. Any GET listing page
can declare a typed parameter and skip the `request.GET.get(...)`
plumbing.

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

### 6. Co-located CSS, JS, and scoped components

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

`pagination` is a template-only component — it has `component.djx`
but no `component.py`. It reads `page_obj` directly from template
context without any Python-side registration. This is valid: a
`component.py` is only needed when the component must compute derived
values or perform DI lookups.

`filter_panel` lives at
[`storefront/catalog/_cards/filter_panel/`](catalog/storefront/catalog/_cards/filter_panel/)
— a nested `_cards/` folder inside the `catalog/` subtree. The
framework's dispatcher registers any `_cards/` directory it encounters
during the route tree walk, scoping each to its containing subtree.
`filter_panel` is therefore visible to `/catalog/` and
`/catalog/<category>/` but invisible on the landing page. The
[`test_filter_panel_scoped_to_catalog`](tests/test_e2e.py) test
verifies that `filter_panel` CSS appears in catalog pages but not on
the landing page.

`filter_panel` also ships
[`component.js`](catalog/storefront/catalog/_cards/filter_panel/component.js).
It does two things. First, it attaches a `change` listener to
`[data-filter-form]` and auto-submits the form when a checkbox or
`<select>` changes (only if the form passes `checkValidity()`).
Number inputs and the text query field are excluded so that
partially-typed values do not trigger a reload mid-entry — those
commit on Enter, and the "Apply filters" button submits everything
at once.

Second, it runs live validation on the query field using the native
Constraint Validation API. The `<input>` declares `minlength="3"`, so
the browser already enforces the rule on submit and shows the
`invalid:` Tailwind variant (rose border on a red-tinted background).
The script layers a contextual help message on top: it reads
`data-help-default` and `data-help-tooshort` from the input,
calls `setCustomValidity()` with a tailored message ("Need 2 more —
at least 3 characters in total"), and updates the help paragraph
with three colour states (`text-slate-500` idle, `text-rose-600` too
short, `text-emerald-600` valid). The empty string is treated as
"no filter" so the user can clear the field without seeing an error.
The script is injected via `{% collect_scripts %}` in
[`storefront/layout.djx`](catalog/storefront/layout.djx).

[`catalog/layout.css`](catalog/storefront/catalog/layout.css) is a
co-located layout stylesheet. It applies `position: sticky; top: 1rem`
to the sidebar on large screens, keeping the filter panel in view as
the product grid scrolls. This style belongs in `layout.css` rather
than a utility class because the `top` offset must be a concrete pixel
value that Tailwind's `top-*` utilities do not express cleanly. The
file is injected automatically for every page in the `catalog/`
subtree through the layout chain and is absent on the landing page,
as the
[`test_catalog_layout_css_absent_on_landing`](tests/test_e2e.py)
test verifies.

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
