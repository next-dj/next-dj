// The entry point of next.poll.min.js, the zone poller, fetched when a scan finds one.

import { createPoller } from "../poll";

window.Next._land("poll", createPoller);
