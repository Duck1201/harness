import type { HarnessClient } from "./HarnessClient";
import { FetchHarnessClient } from "./FetchHarnessClient";

export const harnessClient: HarnessClient = new FetchHarnessClient();

export { FetchHarnessClient, HarnessApiError } from "./FetchHarnessClient";
export type { HarnessClient } from "./HarnessClient";
