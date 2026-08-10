import type { HarnessClient } from "./HarnessClient";
import { FetchHarnessClient } from "./FetchHarnessClient";
import { MockHarnessClient } from "./MockHarnessClient";

export const harnessClient: HarnessClient =
  import.meta.env.VITE_HARNESS_USE_MOCK === "true"
    ? new MockHarnessClient()
    : new FetchHarnessClient();

export { FetchHarnessClient, HarnessApiError } from "./FetchHarnessClient";
export { MockHarnessClient } from "./MockHarnessClient";
export type { HarnessClient } from "./HarnessClient";
