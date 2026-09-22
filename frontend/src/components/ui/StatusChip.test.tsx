import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusChip } from "./StatusChip";

describe("StatusChip", () => {
  it("carries its meaning in text, whatever the tone", () => {
    // Colour alone would be lost to colour-vision deficiency and to a
    // screen reader; the words are the status.
    render(
      <>
        <StatusChip tone="danger">tests_failed</StatusChip>
        <StatusChip>queued</StatusChip>
      </>,
    );
    expect(screen.getByText("tests_failed")).toBeInTheDocument();
    expect(screen.getByText("queued")).toBeInTheDocument();
  });
});
