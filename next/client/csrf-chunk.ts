// The entry point of next.csrf.min.js, which fetches a deferred CSRF token. The runtime
// loads it on the first mutation of a page that shipped only the token endpoint.

import { mintCsrf } from "./csrf";

window.Next._land("csrf", mintCsrf);
