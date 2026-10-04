(() => {
  const tag = document.currentScript;
  const id = tag.dataset.measurementId;

  window.dataLayer = window.dataLayer || [];
  // gtag.js reads the arguments object itself, a rest array would not be recognised.
  window.gtag = function gtag() {
    window.dataLayer.push(arguments);
  };
  window.gtag("js", new Date());
  window.gtag("config", id, { send_page_view: false });

  const loader = document.createElement("script");
  loader.async = true;
  loader.nonce = tag.nonce;
  loader.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(id)}`;
  document.head.append(loader);

  const pageView = ({ url, title }) =>
    window.gtag("event", "page_view", { page_location: url, page_title: title });

  const start = (Next) => {
    pageView({ url: location.href, title: document.title });
    Next.on("next:navigated", (detail) => {
      if (detail.action !== "none") pageView(detail);
    });
  };

  if (window.Next) start(window.Next);
  else document.addEventListener("DOMContentLoaded", () => start(window.Next));
})();
