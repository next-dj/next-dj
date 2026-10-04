// The entry point of next.poll.min.js, the zone poller. The runtime fetches it once a
// scan finds a data-next-poll zone.

import { createPoller } from "./poll";

window.Next._land("poll", createPoller);
