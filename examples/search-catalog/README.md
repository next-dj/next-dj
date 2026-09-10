# Search catalog

A faceted product catalog with search, brand filters, price range, in-stock toggle, sort, and pagination. The listing renders six cards per page with deduplicated co-located CSS, the category and product detail routes thread an inherited `Category` instance through the `[category]/[slug]/` chain without re-querying, and identical search requests share one `LocMemCache` entry for 60 seconds.

The example focuses on the file-router and DI subsystems of next-dj. It dogfoods the core `DQuery[T]` provider that mirrors `DUrl[T]` for query-string parameters. Two domain providers, `DFilters` and `DPage`, build typed snapshots from `request.GET`, with `DFilters` reading the brand list through the same `get_multi_values` helper `DQuery[list[str]]` resolution uses. A `cached_search` helper is keyed by a stable hash of the filter set. An `active_filters` context processor registered on the page backend surfaces a chip strip with a precomputed drop URL per chip. A three-layout chain (`marketplace` then `catalog` then `[category]`) wires the rest of the page tree.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Landing. Three featured products plus a category grid. `?show=N` widens the featured grid, clamped to 12. |
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
uv run python manage.py migrate        # schema only
uv run python manage.py seed_demo      # four categories and 25 products
uv run python manage.py runserver      # http://127.0.0.1:8000/
uv run pytest
```

`seed_demo` writes the demo catalog from [`catalog/demo.py`](catalog/demo.py) — four categories, 24 numbered products, and one `iPhone 15` row the routing walkthrough links to. Migrations carry schema only, so the listing is empty until the command runs.

Tailwind loads via the Play CDN in [`marketplace/layout.djx`](marketplace/layout.djx). No Node, no build step. Components carry co-located CSS and JS that the static collector picks up, deduplicates, and emits exactly once per page. Each listing publishes the set of zones its filter may re-render, so the filter panel auto-submits as you type, the results, the count, the pager, and the chip strip refresh together, and the listing grows on scroll without a full reload. The `filter_panel` component ships a small `component.js` that runs live constraint validation on the search field (minimum 3 characters) through the native Constraint Validation API, rewired through `Next.partial.onMount` so it survives a morphed panel. Every behaviour degrades to a plain GET when the runtime is absent.

## Walking the code

### 1. Why search is plain HTML, not `@action`

Search is idempotent. A bookmark of `?q=iphone&brand=Acme&page=2` should reproduce the same listing. That is the natural shape of `<form method="get">` posting back to the same page, so the live filter stays plain HTML. A form action earns its place only for a discrete jump that should sit in browser history, which is exactly the preset filter in section 8. [`catalog/storefront/catalog/_cards/filter_panel/component.djx`](catalog/storefront/catalog/_cards/filter_panel/component.djx) renders a regular HTML form. The `submit_url` value comes from [`catalog/storefront/catalog/_cards/filter_panel/component.py`](catalog/storefront/catalog/_cards/filter_panel/component.py) which reverses the current category page when scoped, otherwise the all-products listing. Pagination uses [`catalog/templatetags/catalog_qs.py`](catalog/templatetags/catalog_qs.py) to keep every other query parameter intact.

### 2. The `DQuery[T]` provider

[`next/urls/markers.py`](../../next/urls/markers.py) ships a marker that mirrors `DUrl[T]` and reads `request.GET`. Any GET listing page can declare a typed parameter and skip the `request.GET.get(...)` plumbing.

```python
from next import context
from next.urls import DQuery


@context("results")
def search(
    q: DQuery[str] = "", page: DQuery[int] = 1, brand: DQuery[list[str]] = ()
): ...
```

The list form accepts three wire formats. Plain repeated keys `?brand=a&brand=b` produced by `<form method="get">` win first. The qs-style bracket suffix `?brand[]=a&brand[]=b` emitted by axios is the second fallback. The comma-delimited form `?brand=a,b` produced by `qs.stringify` with the comma array format is the third fallback. Empty segments around commas are dropped.

### 3. Domain providers built on top of the core

[`catalog/providers.py`](catalog/providers.py) wraps the raw query string in two typed snapshots so handlers stay declarative.

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

`FiltersProvider.resolve` runs `parse_filters(request)` which is also reused by the active-filter context processor. The brand list inside `parse_filters` calls `next.urls.get_multi_values`, the public helper `QueryParamProvider` reads its own list values through, so all three wire formats (plain repeated, bracket suffix, comma-delimited) reach a hand-written parser exactly as they reach `DQuery[list[str]]`. `PageProvider.resolve` returns a clamped `PageRequest` whose `per_page` is bounded by `MAX_PER_PAGE = 60` and whose page number falls back to 1 on anything unparsable.

The landing page exercises `DQuery` directly. The `featured` callable in [`catalog/storefront/page.py`](catalog/storefront/page.py) accepts an optional `?show=N` parameter through `show: DQuery[int] = DEFAULT_FEATURED`, with no manual `request.GET.get` plumbing. The resolved value is clamped to `MAX_FEATURED = 12`, because a query parameter that sizes a query is user input like any other.

### 4. Three-level nested layouts

The compose chain is automatic. When a listing is rendered the body is substituted into the innermost layout that has a `{% block template %}{% endblock %}` slot, then the next ancestor, and so on. For `/catalog/electronics/iphone-15/` the chain is

```
catalog/storefront/catalog/[category]/[slug]/template.djx
  └─ catalog/storefront/catalog/[category]/layout.djx     # category banner + breadcrumb
      └─ catalog/storefront/catalog/layout.djx            # filter sidebar + chip strip
          └─ marketplace/layout.djx                       # project-level HTML envelope
```

Each layer contributes a meaningful piece of UI. None of the layouts are decorative wrappers added "in case we need them later".

### 5. `inherit_context=True` in a real flow

[`catalog/storefront/catalog/[category]/page.py`](catalog/storefront/catalog/%5Bcategory%5D/page.py) registers `category` as inherit-context. Everything downstream reads that one instance: `page_obj` and `all_brands` on the same page take it as a DI parameter, the breadcrumb in [`[category]/layout.djx`](catalog/storefront/catalog/%5Bcategory%5D/layout.djx) reads it from the template context, and `submit_url` in the `filter_panel` component takes it to decide whether the filter form posts back to a category listing.

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

The parameter is annotated `object` because `_collect_inherited_context` evaluates the callable twice when the page itself is being rendered (once for the URL slug, once for the already-resolved instance from `context_data`). The `isinstance(category, Category)` short-circuit covers the second pass.

The product detail page in [`catalog/storefront/catalog/[category]/[slug]/page.py`](catalog/storefront/catalog/%5Bcategory%5D/%5Bslug%5D/page.py) declares `category: Category` and gets the inherited instance through [`next/pages/context.py`](../../next/pages/context.py)'s `ContextByDefaultProvider`. No re-query, no kwargs threading, no helper.

### 6. Co-located CSS, JS, and scoped components

`product_card` is rendered six times on the catalog listing and three more times on the landing page. Its [`component.css`](catalog/storefront/_cards/product_card/component.css) emits `transition` and `:hover` rules. The static collector emits exactly one `<link rel="stylesheet">` per asset URL across both pages. The [`tests/test_integration.py::TestRouting::test_product_card_css_dedup`](tests/test_integration.py) and [`test_landing_reuses_product_card_css`](tests/test_integration.py) tests assert this directly. The component lives at [`storefront/_cards/`](catalog/storefront/_cards/) one level above the catalog tree because both the landing and the listing render it.

`pagination` is a template-only component — it has `component.djx` but no `component.py`. It reads `page_obj` directly from template context without any Python-side registration. This is valid: a `component.py` is only needed when the component must compute derived values or perform DI lookups.

`filter_panel` lives at [`storefront/catalog/_cards/filter_panel/`](catalog/storefront/catalog/_cards/filter_panel/) — a nested `_cards/` folder inside the `catalog/` subtree. The file router's page-tree walk offers every folder named by `COMPONENTS_DIR` to the components backend, which registers it scoped to the subtree that contains it. `filter_panel` is therefore visible to `/catalog/` and `/catalog/<category>/` but invisible on the landing page. The [`test_filter_panel_scoped_to_catalog`](tests/test_integration.py) test verifies that `filter_panel` CSS appears in catalog pages but not on the landing page.

`filter_panel` also ships [`component.js`](catalog/storefront/catalog/_cards/filter_panel/component.js). It runs live validation on the query field using the native Constraint Validation API. The `<input>` declares `minlength="3"`, so the browser already enforces the rule on submit and paints the field with the `invalid:border-destructive` Tailwind variant. The script layers a contextual help message on top: it reads `data-help-default` and `data-help-tooshort` from the input, calls `setCustomValidity()` with a tailored message ("Need 2 more — at least 3 characters in total"), and updates the help paragraph with three colour states (`text-slate-500` idle, `text-rose-600` too short, `text-emerald-600` valid). The empty string is treated as "no filter" so the user can clear the field without seeing an error.

The script registers its work through `Next.partial.onMount`, not a `document.querySelectorAll` scan at load. The runtime runs the callback over the initial DOM and over every subtree it later inserts, so a panel that arrives in a morphed zone is wired the same way the first render was, with no listener orphaned by the swap. The script is injected via `{% collect_scripts %}` in [`marketplace/layout.djx`](marketplace/layout.djx).

### 7. The zone set a page publishes, auto-submit, and infinite scroll

The filter panel is rendered by [`catalog/storefront/catalog/layout.djx`](catalog/storefront/catalog/layout.djx), the layout every page under `/catalog/` shares, product detail included. A target hardcoded in the panel would therefore ask the detail page for zones only a listing declares, so the zone set is page state rather than component state. Each listing page publishes it as a page-local `@context` named `filter_zones`, and the panel renders its partial attributes only when that key is set.

```python
@context("filter_zones")
def filter_zones() -> str:
    return zone_target(LISTING_ZONES)
```

`inherit_context` stays off, so the value stops at the page that declares it. [`catalog/storefront/catalog/page.py`](catalog/storefront/catalog/page.py) publishes `catalog-results,catalog-more,catalog-count,catalog-pager,catalog-chips`, [`[category]/page.py`](catalog/storefront/catalog/%5Bcategory%5D/page.py) drops `catalog-more` because that listing paginates instead of growing on scroll, and [`[category]/[slug]/page.py`](catalog/storefront/catalog/%5Bcategory%5D/%5Bslug%5D/page.py) publishes nothing. On the product detail page the same panel therefore renders without `data-next-target`, `data-next-trigger`, or `data-next-debounce` and submits as a plain GET that navigates to the listing. The names live once in [`catalog/zones.py`](catalog/zones.py) so the preset form of section 8 morphs exactly the set the filter asks for.

Zone by zone, [`catalog/storefront/catalog/template.djx`](catalog/storefront/catalog/template.djx) wraps the product grid in `{% zone "catalog-results" tag="ul" %}`, the "Show more" sentinel in a sibling `{% zone "catalog-more" %}`, the header count in `{% zone "catalog-count" %}`, and the pagination component in `{% zone "catalog-pager" %}`. The `tag="ul"` keeps the results wrapper a real list element so the `<li>` rows stay valid children — a `<div>` would be dropped by the parser. The chip strip sits in `{% zone "catalog-chips" %}` inside the shared layout, and the zone wraps the whole `{% if active_filters %}` rather than its body: a zone nested inside a conditional disappears from the first render, and the filter would then target a zone the page never emitted. An unfiltered listing renders the wrapper empty, which [`catalog/layout.css`](catalog/storefront/catalog/layout.css) collapses with `:not(:has(ul))`. [`[category]/template.djx`](catalog/storefront/catalog/%5Bcategory%5D/template.djx) mirrors the results, count, and pager zones without the sentinel, and its rows carry the same `data-next-key` so a morph re-uses the DOM nodes it can.

As you type, the runtime debounces, issues one GET for the whole published set, morphs the list, the sentinel, the count, the pager, and the chips in place, and syncs the query string with `replaceState`. Narrowing to `?q=iphone` therefore leaves no stale "25 products" in the header and no "Page 1 of 5" under a single card. The catalog page never changed: the `page_obj` provider still reads `request.GET` through `DFilters`/`DPage` and the chips still come from the context processor, so the same view answers both the full page and the zone request.

When the listing has another page the `catalog-more` zone renders a sentinel `<a id="results-sentinel">` with `data-next-merge="append"` and `data-next-lazy="revealed"`. The sentinel lives outside the results zone on purpose: an `append` patch grows a zone by adding its incoming children at the end, so a marker that must stay last cannot ride inside the appended list. The sentinel targets only `catalog-results` and `catalog-more`, not the full published set, because an append merge grafts incoming children onto a zone and appending a second count or pager would duplicate it. The runtime fires the GET when the sentinel scrolls into view, targeting those two zones with the append intent. The server answers with one `append` per zone: the `catalog-results` patch carries only the next page of rows, deduplicated by their `data-next-key`, and the `catalog-more` patch replaces the single `results-sentinel` link in place so its `href` advances to the following page. The rows therefore always land after the accumulated list and the sentinel always trails them. The `Vary` header always lists `X-Next-Merge`, so a shared cache never hands an append envelope to a client that asked for a morph. Changing the search term re-morphs both zones and the accumulated list resets on its own. On the last page the zone renders an inert `<p id="results-sentinel" data-catalog-end>` instead of the link. The id is load-bearing: an `append` merge only ever replaces a matching child or adds a new one, it never removes, so an empty `catalog-more` body would leave the spent link hanging in the DOM. Keeping the id on the end marker lets the same append swap the link out for the closing note, and because the marker carries no `data-next-*` attribute nothing observes or fetches from it again.

Without the runtime the form is a plain `<form method="get">` and the sentinel is an honest pagination link, so the catalog stays bookmarkable and crawlable.

[`catalog/layout.css`](catalog/storefront/catalog/layout.css) is co-located with the layout, so it loads for every page in the `catalog/` subtree and for none outside it, which [`test_catalog_layout_css_absent_on_landing`](tests/test_integration.py) pins. It holds the two rules that have no element to hang a utility class on: the results grid, keyed by `ul[data-next-zone="catalog-results"]` so it styles the list whether it renders inline or arrives as a zone patch, and the `:not(:has(ul))` collapse that hides the empty chip strip. The sticky sidebar rule sits beside them, next to the layout that renders the sidebar.

### 8. Preset filters that push history

The live filter syncs the query string with `replaceState`, the right choice for a value that changes on every keystroke. A preset filter is the opposite, a single deliberate jump to a named view, so it earns a real history entry the back button can return from. That is the one place the catalog reaches for a POST `@action`. [`catalog/forms.py`](catalog/forms.py) registers `preset_filter_form`, a `Form` whose `preset` field maps to a canonical querystring such as `?sort=price_asc`. The preset bar in [`catalog/storefront/catalog/template.djx`](catalog/storefront/catalog/template.djx) renders the form with `zone=filter_zones`, the same page-local key the live filter reads, so an apply refreshes the listing exactly as far as a keystroke does.

```python
request.GET = QueryDict(mutable=True)
request.GET.update(params)
patches = Patches(request).push_url(target)
for zone in LISTING_ZONES:
    patches.morph(zone=zone)
return patches.response()
```

The handler points `request.GET` at the preset's params before morphing, so the cached search, the product count, the active-filter chips, and the pagination all agree with the URL `push_url` writes to history. `push_url` validates the href against the request host, so the envelope carries a `url` op with `action: "push"` and a same-site target. Without the runtime the apply falls back to a redirect to the same canonical URL, so the preset stays a plain link.

### 9. `cached_search` and the LocMem hit path

[`catalog/queries.py`](catalog/queries.py) materialises the page slice into a list so the cached payload does not depend on a queryset closure that can grow stale across requests. The cache key is a stable blake2b hash of a sorted JSON encoding of the filter set, page number, page size, and category PK. Two identical GETs produce one cache key and serve the second request from memory for `CACHE_TTL = 60` seconds, which the [`tests/test_integration.py::TestCacheHit`](tests/test_integration.py) tests verify through the cache backend's internal map.

The database carries the same filter set. `Product` declares `db_index=True` on `brand` and two composite indexes, `product_cat_stock_idx` over `(category, in_stock)` and `product_price_idx` over `price`, which is what the category listing and the price range read. Both names are spelled out rather than left to Django, because the auto-generated name is truncated to a cap that moved between Django versions and an unnamed index would drift under `makemigrations --check` on part of the support matrix. [`0003_explicit_index_names`](catalog/migrations/0003_explicit_index_names.py) renames the two indexes an older run had created.

### 10. Active filter chips

[`catalog/context_processors.py`](catalog/context_processors.py) returns an `active_filters` list where every chip carries its `label`, `key`, `value`, and a precomputed `drop_url`. It is registered on the page backend, under `PAGE_BACKENDS[0]["OPTIONS"]["context_processors"]` in [`config/settings.py`](config/settings.py), so it runs for file-routed pages and zone renders alike while Django's own `TEMPLATES` list stays untouched. The `drop_url` is the current query string with that one pair removed, so the layout renders the anchor as `href="?{{ chip.drop_url }}"` and never looks a URL up by hand. Clicking a chip drops one filter at a time without disturbing the rest. The strip lives in the `catalog-chips` zone, so a live filter grows and shrinks it in place.

The `sort` key never becomes a chip. It always carries a value, so a chip for it would be permanent noise rather than something a click can drop.

## Further reading

- [`next/urls/markers.py`](../../next/urls/markers.py) ships both the `DUrl` provider for URL path segments and the `DQuery` provider for query-string parameters. The narrative section lives in [`docs/content/topics/dependency-injection.rst`](../../docs/content/topics/dependency-injection.rst).
- [`next/pages/registry.py`](../../next/pages/registry.py) hosts the `_collect_inherited_context` walk used by `inherit_context=True`.
- [`next/pages/context.py`](../../next/pages/context.py) hosts the `ContextByDefaultProvider` that injects inherited values into downstream callables by parameter name.
- [`next/static/collector.py`](../../next/static/collector.py) hosts the static collector that deduplicates co-located component CSS.
