// The entry point of next.sse.min.js, the server-sent events bridge. The runtime
// fetches it once a scan finds a data-next-sse container.

import { createSse } from "./sse";

window.Next._land("sse", createSse);
