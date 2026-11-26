import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Tabs } from "./Tabs";
import { panelId } from "./tabIds";

type Pick3 = "one" | "two" | "three";

function Harness() {
  const [value, setValue] = useState<Pick3>("one");

  return (
    <>
      <Tabs
        group="t"
        label="Things"
        items={[
          { value: "one", label: "One" },
          { value: "two", label: "Two", badge: <span>(5)</span> },
          { value: "three", label: "Three" },
        ]}
        value={value}
        onChange={setValue}
      />
      <div role="tabpanel" id={panelId("t", value)} aria-label="panel">
        {value}
      </div>
    </>
  );
}

describe("Tabs", () => {
  it("selects a tab on click and points it at its panel", () => {
    render(<Harness />);

    fireEvent.click(screen.getByRole("tab", { name: /two/i }));

    const tab = screen.getByRole("tab", { name: /two/i });
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(tab).toHaveAttribute("aria-controls", "t-panel-two");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("two");
  });

  it("keeps only the selected tab in the tab order", () => {
    render(<Harness />);

    expect(screen.getByRole("tab", { name: "One" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Three" })).toHaveAttribute("tabindex", "-1");
  });

  it("moves with the arrow keys, wrapping at the ends", () => {
    render(<Harness />);
    const list = screen.getByRole("tablist", { name: "Things" });

    fireEvent.keyDown(list, { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "Three" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Three" })).toHaveFocus();

    fireEvent.keyDown(list, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "One" })).toHaveAttribute("aria-selected", "true");
  });

  it("jumps to the first and last tab with Home and End", () => {
    render(<Harness />);
    const list = screen.getByRole("tablist", { name: "Things" });

    fireEvent.keyDown(list, { key: "End" });
    expect(screen.getByRole("tab", { name: "Three" })).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(list, { key: "Home" });
    expect(screen.getByRole("tab", { name: "One" })).toHaveAttribute("aria-selected", "true");
  });

  it("ignores other keys", () => {
    render(<Harness />);

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "a" });

    expect(screen.getByRole("tab", { name: "One" })).toHaveAttribute("aria-selected", "true");
  });
});
