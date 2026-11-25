import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { usePref } from "./usePref";

const SIDES = ["left", "right"] as const;

describe("usePref", () => {
  beforeEach(() => window.localStorage.clear());

  it("starts from the stored value", () => {
    window.localStorage.setItem("agentshowdown.side", "right");

    const { result } = renderHook(() => usePref("side", SIDES, "left"));

    expect(result.current[0]).toBe("right");
  });

  it("updates and remembers a new value", () => {
    const { result } = renderHook(() => usePref("side", SIDES, "left"));

    act(() => result.current[1]("right"));

    expect(result.current[0]).toBe("right");
    expect(renderHook(() => usePref("side", SIDES, "left")).result.current[0]).toBe("right");
  });
});
