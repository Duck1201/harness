import { describe, expect, it } from "vitest";
import { withLoopbackPair } from "./App";

describe("withLoopbackPair", () => {
  it("suggests the other spelling of the same loopback origin", () => {
    expect(withLoopbackPair(["http://127.0.0.1:8765"])).toEqual([
      "http://127.0.0.1:8765",
      "http://localhost:8765",
    ]);
    expect(withLoopbackPair(["http://localhost:8765"])).toEqual([
      "http://localhost:8765",
      "http://127.0.0.1:8765",
    ]);
  });

  it("keeps the port, adds nothing twice and leaves other hosts alone", () => {
    expect(
      withLoopbackPair(["http://localhost:8765", "http://127.0.0.1:8765"]),
    ).toEqual(["http://localhost:8765", "http://127.0.0.1:8765"]);
    expect(withLoopbackPair(["https://127.0.0.1:9000"])).toEqual([
      "https://127.0.0.1:9000",
      "https://localhost:9000",
    ]);
    expect(withLoopbackPair(["https://harness.example"])).toEqual([
      "https://harness.example",
    ]);
  });

  it("passes a line the Operator is still typing through untouched", () => {
    expect(withLoopbackPair(["nao-e-url"])).toEqual(["nao-e-url"]);
    expect(withLoopbackPair([])).toEqual([]);
  });
});
