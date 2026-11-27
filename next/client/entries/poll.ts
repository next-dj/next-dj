// The entry point of next.poll.min.js, the zone poller, fetched when a scan finds one.

import { createPoller } from "../poll";

window.Next._extend("poll", () => createPoller, __BUILD__);
