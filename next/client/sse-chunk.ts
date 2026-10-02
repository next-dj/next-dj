// The entry of next.sse.min.js, the stream bridge. The runtime fetches it once a scan
// finds a data-next-sse container, and this module hands itself over on evaluation.

import { createSse } from "./sse";

window.Next._land("sse", createSse);
