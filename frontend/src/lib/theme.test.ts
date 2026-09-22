import { renderHook, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import indexHtml from "../../index.html?raw";
import { useTheme } from "../hooks/useTheme";
import { THEME_STORAGE_KEY, applyTheme, readStoredTheme, storeTheme } from "./theme";

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  vi.restoreAllMocks();
});

describe("theme storage", () => {
  it("defaults to following the system when nothing is saved", () => {
    expect(readStoredTheme()).toBe("system");
  });

  it("remembers an explicit choice in this browser", () => {
    storeTheme("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(readStoredTheme()).toBe("dark");
  });

  it("stores nothing for system, so the OS setting applies", () => {
    storeTheme("light");
    storeTheme("system");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it("ignores a saved value it does not recognise", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "sepia");
    expect(readStoredTheme()).toBe("system");
  });

  it("falls back to system when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    expect(readStoredTheme()).toBe("system");
  });

  it("still applies a choice when it cannot be saved", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    expect(() => storeTheme("dark")).not.toThrow();
  });
});

describe("applyTheme", () => {
  it("stamps an explicit choice on the root and clears it for system", () => {
    applyTheme("dark");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    applyTheme("system");
    expect(document.documentElement).not.toHaveAttribute("data-theme");
  });
});

describe("useTheme", () => {
  it("starts from the saved choice, then applies and saves changes", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    const { result } = renderHook(() => useTheme());

    expect(result.current[0]).toBe("light");
    expect(document.documentElement).toHaveAttribute("data-theme", "light");

    act(() => result.current[1]("dark"));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });
});

describe("index.html pre-paint script", () => {
  it("reads the same storage key as the app", () => {
    // If these drift, a saved theme would flash the wrong way on every load.
    expect(indexHtml).toContain(`"${THEME_STORAGE_KEY}"`);
  });
});
