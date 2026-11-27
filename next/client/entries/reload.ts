// The entry point of next.reload.min.js, the loading UI of reloaded zones and layers.

import { createReload } from "../reload";

window.Next._extend(
  "reload",
  (kernel) => {
    createReload(kernel);
    return true;
  },
  __BUILD__,
);
