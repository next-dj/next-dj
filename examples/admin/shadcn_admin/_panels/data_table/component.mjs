/**
 * Toggle every `[name="_selected_action"]` checkbox in the table from the header
 * checkbox. Wired up at module load so all data tables in a page work without
 * per-instance setup.
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
