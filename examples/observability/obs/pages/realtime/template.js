document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-obs-nudge]");
  if (!button) return;
  window.Next?.partial?.stream?.send(
    button.dataset.obsNudge,
    "nudge",
    {},
    { ack: false },
  );
});
