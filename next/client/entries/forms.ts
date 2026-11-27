// The entry point of next.forms.min.js, inline validation and filter forms, fetched once
// a mount finds a validated form or a trigger.

import { createForms } from "../forms";

window.Next._extend("forms", () => createForms, __BUILD__);
