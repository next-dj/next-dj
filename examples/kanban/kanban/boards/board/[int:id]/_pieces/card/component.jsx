/* React enhancement for kanban cards. Wires HTML5 drag events on each
 * server-rendered card and POSTs to the move_card form action with the
 * CSRF token from window.Next.context.board.csrf. */
(function () {
  function attach() {
    const ctx = (window.Next && window.Next.context && window.Next.context.board) || {};
    const csrf = ctx.csrf;
    document.querySelectorAll('[data-kanban-card]').forEach((node) => {
      node.addEventListener('dragstart', (event) => {
        event.dataTransfer.setData('text/card-id', node.dataset.kanbanCard);
      });
    });
    const moveUrl = ctx.move_card_url;
    document.querySelectorAll('[data-kanban-column]').forEach((column) => {
      column.addEventListener('drop', (event) => {
        event.preventDefault();
        delete column.dataset.drop;
        const cardId = event.dataTransfer.getData('text/card-id');
        if (!cardId || !moveUrl) return;
        const targetColumnId = column.dataset.kanbanColumn;
        const cards = column.querySelectorAll('[data-kanban-card]');
        const targetPosition = cards.length;
        const body = new URLSearchParams({
          card_id: cardId,
          target_column_id: targetColumnId,
          target_position: String(targetPosition),
          csrfmiddlewaretoken: csrf || '',
        });
        fetch(moveUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-CSRFToken': csrf || '',
          },
          body: body.toString(),
          credentials: 'same-origin',
        }).then(() => window.location.reload());
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attach);
  } else {
    attach();
  }
})();
