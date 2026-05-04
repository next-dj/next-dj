/* React enhancement for kanban columns. Reads merged state from
 * window.Next.context.board and decorates server-rendered columns with
 * drop-target highlighting on dragover events. */
(function () {
  function attach() {
    const ctx = (window.Next && window.Next.context && window.Next.context.board) || {};
    const columns = (ctx.columns || []);
    document.querySelectorAll('[data-kanban-column]').forEach((node) => {
      const id = parseInt(node.dataset.kanbanColumn, 10);
      const meta = columns.find((c) => c.id === id);
      if (!meta) return;
      node.addEventListener('dragover', (event) => {
        event.preventDefault();
        node.dataset.drop = 'active';
      });
      node.addEventListener('dragleave', () => {
        delete node.dataset.drop;
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attach);
  } else {
    attach();
  }
})();
