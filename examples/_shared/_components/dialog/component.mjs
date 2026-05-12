/* Wire up native <dialog> elements to open/close triggers.
   Open: <button data-ui-dialog-open="<dialog-id>">.
   Close: any button inside the dialog with data-ui-dialog-close,
   or pressing Esc, or clicking the backdrop. */

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;

  const opener = target.closest("[data-ui-dialog-open]");
  if (opener instanceof HTMLElement) {
    const id = opener.dataset.uiDialogOpen;
    if (id) {
      const dialog = document.getElementById(id);
      if (dialog instanceof HTMLDialogElement) {
        dialog.showModal();
      }
    }
    return;
  }

  const closer = target.closest("[data-ui-dialog-close]");
  if (closer) {
    const dialog = closer.closest("dialog");
    if (dialog instanceof HTMLDialogElement) {
      dialog.close();
    }
    return;
  }

  if (target instanceof HTMLDialogElement) {
    const rect = target.getBoundingClientRect();
    const inside =
      event.clientX >= rect.left &&
      event.clientX <= rect.right &&
      event.clientY >= rect.top &&
      event.clientY <= rect.bottom;
    if (!inside) target.close();
  }
});
