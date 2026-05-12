/* Click-outside handler for ui/dropdown <details> elements.
   Native <details> already toggles on click of the summary. This
   module just closes any open dropdown when the user clicks outside
   of it so the UX matches what users expect from shadcn DropdownMenu. */
document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const open = document.querySelectorAll("details[open] > [data-ui-dropdown-menu]");
  for (const menu of open) {
    const details = menu.parentElement;
    if (!details) continue;
    if (!details.contains(target)) {
      details.removeAttribute("open");
    }
  }
});
