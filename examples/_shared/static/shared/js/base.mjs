/* Site-wide keyboard shortcut for the examples catalog. Pressing "/" outside
   a text field focuses the first search input on the page, the way the search
   boxes in admin, wiki, and search-catalog expect.

   This module belongs to no single component, so it is registered from the
   shared page_head with {% use_module %} rather than co-located next to a
   component.djx. A single listener on document survives a morph that replaces
   the search markup, where a per-element listener would be lost. */

const SEARCH_SELECTOR = 'input[type="search"], input[name="q"]';

document.addEventListener("keydown", (event) => {
  if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
  const active = document.activeElement;
  if (active instanceof HTMLInputElement) return;
  if (active instanceof HTMLTextAreaElement) return;
  if (active instanceof HTMLElement && active.isContentEditable) return;
  const search = document.querySelector(SEARCH_SELECTOR);
  if (!(search instanceof HTMLInputElement)) return;
  event.preventDefault();
  search.focus();
  search.select();
});
