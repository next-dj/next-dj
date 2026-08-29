/**
 * Toggle every `[name="_selected_action"]` checkbox from the header checkbox.
 * Wired up at module load, so every data table in a page works unconfigured.
 */
function initBulkToggles(root) {
  for (const master of root.querySelectorAll("[data-bulk-toggle]")) {
    const table = master.closest("table");
    if (!table) continue;
    master.addEventListener("change", () => {
      const checked = master.checked;
      for (const cb of table.querySelectorAll('input[name="_selected_action"]')) {
        cb.checked = checked;
      }
    });
  }
}

initBulkToggles(document);
