// The entry point of next.intent.min.js, the link and layer prefetch and the active
// links pass, which the router loads once the page is idle.

import { createLinks } from "../links";
import { createPrefetch } from "../prefetch";

window.Next._extend(
  "intent",
  () => (host) => {
    createLinks(host).first();
    return createPrefetch(host);
  },
  __BUILD__,
);
