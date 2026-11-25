import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Segmented } from "./Segmented";

function Harness() {
  const [value, setValue] = useState<"a" | "b">("a");

  return (
    <>
      <Segmented
        label="Mode"
        options={[
          { value: "a", label: "Alpha" },
          { value: "b", label: "Beta" },
        ]}
        value={value}
        onChange={setValue}
      />
      <output>{value}</output>
    </>
  );
}

describe("Segmented", () => {
  it("shows every option with the current one checked", () => {
    render(<Harness />);

    expect(screen.getByRole("radiogroup", { name: "Mode" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Alpha" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "Beta" })).toHaveAttribute("aria-checked", "false");
  });

  it("picks an option in one click", () => {
    render(<Harness />);

    fireEvent.click(screen.getByRole("radio", { name: "Beta" }));

    expect(screen.getByRole("radio", { name: "Beta" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("status")).toHaveTextContent("b");
  });
});
