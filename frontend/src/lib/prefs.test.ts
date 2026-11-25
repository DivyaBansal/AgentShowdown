import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readPref, storePref } from "./prefs";

const MODES = ["state", "feature"] as const;

describe("prefs", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("round-trips a stored choice", () => {
    storePref("mode", "feature");

    expect(readPref("mode", MODES, "state")).toBe("feature");
  });

  it("namespaces keys under the app", () => {
    storePref("mode", "feature");

    expect(window.localStorage.getItem("agentshowdown.mode")).toBe("feature");
  });

  it("falls back when nothing is stored", () => {
    expect(readPref("mode", MODES, "state")).toBe("state");
  });

  it("falls back when the stored value is no longer an option", () => {
    window.localStorage.setItem("agentshowdown.mode", "colour");

    expect(readPref("mode", MODES, "state")).toBe("state");
  });

  it("falls back when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });

    expect(readPref("mode", MODES, "state")).toBe("state");
  });

  it("does not throw when storage refuses a write", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });

    expect(() => storePref("mode", "feature")).not.toThrow();
  });
});
