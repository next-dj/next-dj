// The entry of next.poll.min.js, the zone poller. The runtime fetches it once a scan
// finds a data-next-poll zone, and this module hands itself over on evaluation.

import { createPoller } from "./poll";

window.Next._land("poll", createPoller);
