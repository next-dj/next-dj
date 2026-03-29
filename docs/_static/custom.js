// Shibuya calls scrollIntoViewIfNeeded on `.globaltoc a.current`, which can push
// the last items in the next section (e.g. System checks under API Reference)
// below the visible part of the fixed-height sidebar when the current page is
// lower in the Guide list.
window.addEventListener("load", () => {
  const scrollbar = document.querySelector(".sy-lside .sy-scrollbar");
  const systemChecks = document.querySelector(
    '.globaltoc a[href*="system-checks"]',
  );
  if (!scrollbar || !systemChecks) {
    return;
  }
  const margin = 8;
  const sb = scrollbar.getBoundingClientRect();
  const link = systemChecks.getBoundingClientRect();
  if (link.bottom > sb.bottom - margin) {
    scrollbar.scrollTop += link.bottom - sb.bottom + margin;
  }
});
