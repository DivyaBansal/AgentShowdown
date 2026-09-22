import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useState } from "react";
import type { ThemeChoice } from "../lib/theme";
import { ThemeSwitch } from "./ThemeSwitch";

function Harness({ initial }: { initial: ThemeChoice }) {
  const [value, setValue] = useState<ThemeChoice>(initial);
  return <ThemeSwitch value={value} onChange={setValue} />;
}

describe("ThemeSwitch", () => {
  it("offers system, light and dark as a labelled group", () => {
    render(<Harness initial="system" />);
    const group = screen.getByRole("group", { name: /theme/i });
    expect(group).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /match system/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /light theme/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /dark theme/i })).toBeInTheDocument();
  });

  it("marks exactly the current choice as pressed", () => {
    render(<Harness initial="dark" />);
    expect(screen.getByRole("button", { name: /dark theme/i })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /light theme/i })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: /match system/i })).toHaveAttribute("aria-pressed", "false");
  });

  it("moves the pressed state to the button clicked", () => {
    render(<Harness initial="system" />);
    fireEvent.click(screen.getByRole("button", { name: /light theme/i }));
    expect(screen.getByRole("button", { name: /light theme/i })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /match system/i })).toHaveAttribute("aria-pressed", "false");
  });

  it("adds no images that would be announced", () => {
    // Icons are decorative; each button is named by its label.
    render(<Harness initial="system" />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
