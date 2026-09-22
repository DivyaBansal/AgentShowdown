import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BrandMark } from "./BrandMark";

describe("BrandMark", () => {
  it("stays out of the accessibility tree", () => {
    // The wordmark beside it names the app. Exposing the mark as an image
    // would announce the name twice and add a stray role="img" next to the
    // comparison charts.
    render(<BrandMark />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("draws one sword per contender in the first two series colours", () => {
    // The mark shares its colours with the charts, and follows the theme
    // through the tokens rather than fixed hex values.
    const { container } = render(<BrandMark />);
    const fills = [...container.querySelectorAll("svg > g")].map((g) => g.getAttribute("fill"));
    expect(fills).toEqual(["var(--series-1)", "var(--series-2)"]);
  });

  it("renders at the requested size", () => {
    const { container } = render(<BrandMark size={16} />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("width", "16");
    expect(svg).toHaveAttribute("height", "16");
  });
});
