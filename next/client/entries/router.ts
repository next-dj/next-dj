// The entry point of next.router.min.js, the client router with its history, declared
// by every routed document. Once it evaluates, Next.router exists.

import { createRouter } from "../router";

window.Next._extend("router", (kernel) => createRouter(kernel), __BUILD__);
