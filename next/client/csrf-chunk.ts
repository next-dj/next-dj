// The entry of next.csrf.min.js, the mint of a deferred token. The runtime fetches it
// on the first need of a page that shipped only the endpoint.

import { mintCsrf } from "./csrf";

window.Next._land("csrf", mintCsrf);
