(() => {
  const tag = document.currentScript;

  // The queue stub fbevents.js drains once it loads, the shape of Meta's own snippet.
  const fbq = function fbq(...args) {
    if (fbq.callMethod) fbq.callMethod(...args);
    else fbq.queue.push(args);
  };
  Object.assign(fbq, { push: fbq, loaded: true, version: "2.0", queue: [] });
  window.fbq = window._fbq = fbq;

  const loader = document.createElement("script");
  loader.async = true;
  loader.nonce = tag.nonce;
  loader.src = "https://connect.facebook.net/en_US/fbevents.js";
  document.head.append(loader);

  fbq("init", tag.dataset.pixelId);

  const start = (Next) => {
    fbq("track", "PageView");
    Next.on("next:navigated", (detail) => {
      if (detail.action !== "none") fbq("track", "PageView");
    });
  };

  if (window.Next) start(window.Next);
  else document.addEventListener("DOMContentLoaded", () => start(window.Next));
})();
