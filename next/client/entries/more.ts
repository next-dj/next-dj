// The entry point of next.more.min.js, the address sync and pruning of `{% more %}`.

import { createMore } from "../more";

window.Next._extend(
  "more",
  (kernel) => {
    createMore(kernel);
    return true;
  },
  __BUILD__,
);
