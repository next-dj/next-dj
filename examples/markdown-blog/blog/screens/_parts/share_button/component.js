document.querySelectorAll("[data-share]").forEach((button) => {
  button.addEventListener("click", async () => {
    const post = window.Next?.context?.post;
    if (!post) return;
    const payload = `${post.title} — ${location.href}`;
    try {
      await navigator.clipboard.writeText(payload);
      button.textContent = "✓ Copied";
    } catch {
      button.textContent = "✗ Copy failed";
    }
  });
});
