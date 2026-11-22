import { once } from "../protocol";
import { createReader } from "../reader";

once("reader", () => createReader(window.Next));
