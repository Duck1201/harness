import { describe, expect, it } from "vitest";
import { harnessClient } from ".";
import { FetchHarnessClient } from "./FetchHarnessClient";

describe("client composition", () => {
  it("usa o client live por padrão", () => {
    expect(harnessClient).toBeInstanceOf(FetchHarnessClient);
  });
});
