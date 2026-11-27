// The entry point of next.boundaries.min.js, the reader of streamed answers and the
// client trip of deferred boundaries, declared by a page that streams or defers. A
// streamed boundary that fails or ends incomplete is retried by the same bundle.

import { createDefer } from "../defer";
import { createReader } from "../reader";

window.Next._extend(
  "boundaries",
  (kernel) => {
    createReader(kernel);
    createDefer(kernel);
    return true;
  },
  __BUILD__,
);
