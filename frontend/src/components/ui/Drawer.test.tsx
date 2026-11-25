import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Drawer, EXIT_MS } from "./Drawer";

function Harness({ onClose = () => {} }: { onClose?: () => void }) {
  const [item, setItem] = useState<string | null>(null);

  return (
    <>
      <button type="button" onClick={() => setItem("first")}>
        Open
      </button>
      <button type="button" onClick={() => setItem("second")}>
        Switch
      </button>
      <Drawer
        open={item !== null}
        label="Details"
        onClose={() => {
          onClose();
          setItem(null);
        }}
      >
        {item && <p>Showing {item}</p>}
      </Drawer>
    </>
  );
}

describe("Drawer", () => {
  afterEach(() => vi.useRealTimers());

  it("renders nothing while closed", () => {
    render(<Harness />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens beside the page, takes focus and leaves the page usable", () => {
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    const drawer = screen.getByRole("dialog", { name: "Details" });
    expect(drawer).toHaveFocus();
    expect(drawer).toHaveAttribute("aria-modal", "false");

    fireEvent.click(screen.getByRole("button", { name: "Switch" }));
    expect(screen.getByText("Showing second")).toBeInTheDocument();
  });

  it("closes on Escape and hands focus back", () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    const opener = screen.getByRole("button", { name: "Open" });
    opener.focus();
    fireEvent.click(opener);

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).toHaveBeenCalledOnce();
    expect(opener).toHaveFocus();
  });

  it("keeps the last content on screen while it slides out", () => {
    vi.useFakeTimers();
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    fireEvent.keyDown(document, { key: "Escape" });

    // The caller has already dropped its content; the drawer still shows it.
    expect(screen.getByRole("dialog")).toHaveClass("drawer--leaving");
    expect(screen.getByText("Showing first")).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(EXIT_MS));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("skips the exit wait when motion is reduced", () => {
    vi.useFakeTimers();
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce") }));
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    fireEvent.keyDown(document, { key: "Escape" });
    act(() => vi.advanceTimersByTime(0));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("stops listening for Escape once closed", () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    fireEvent.keyDown(document, { key: "Escape" });
    act(() => vi.advanceTimersByTime(EXIT_MS));

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).toHaveBeenCalledOnce();
  });
});
