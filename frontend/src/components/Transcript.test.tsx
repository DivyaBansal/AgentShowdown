import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { Transcript } from "./Transcript";

describe("Transcript", () => {
  it("renders each entry's kind and text", () => {
    render(
      <Transcript
        entries={[
          { index: 0, kind: "assistant", text: "Reading the file..." },
          { index: 1, kind: "result", text: "Added the missing import." },
        ]}
      />,
    );
    expect(screen.getByText("assistant")).toBeInTheDocument();
    expect(screen.getByText("Reading the file...")).toBeInTheDocument();
    expect(screen.getByText("result")).toBeInTheDocument();
    expect(screen.getByText("Added the missing import.")).toBeInTheDocument();
  });

  it("renders text without a kind badge when the event had no type", () => {
    render(<Transcript entries={[{ index: 0, kind: null, text: "done" }]} />);
    expect(screen.getByText("done")).toBeInTheDocument();
  });

  it("says so when there are no structured events", () => {
    render(<Transcript entries={[]} />);
    expect(screen.getByText(/no structured events/i)).toBeInTheDocument();
  });
});
