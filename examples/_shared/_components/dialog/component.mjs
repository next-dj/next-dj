/* Styling shell over a native <dialog>. The framework's layer runtime owns
   opening a dialog from a data-next-layer link and closing it on accept or
   dismiss, so this component ships no open trigger of its own.

   What stays is the document-delegation idiom: a single listener on document
   survives a morph that replaces the dialog markup, where a per-element
   listener would be lost. It closes a standalone styled dialog from its
   close button or a backdrop click, for the cases that mount a <dialog>
   directly without going through a layer. */

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;

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
