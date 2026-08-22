/* Focus the first search input when "/" is pressed outside a text field.
   The shortcut belongs to no single component, so page_head registers it with
   use_module instead of co-locating it. A single listener on document survives
   a morph that replaces the search markup, where a per-element one would not. */

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
