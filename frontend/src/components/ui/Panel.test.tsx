import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Panel } from "./Panel";

function details(): HTMLDetailsElement {
  return screen.getByRole("heading", { name: "Secrets" }).closest("details")!;
}

describe("Panel", () => {
  it("names the section with a heading and shows its status in words", () => {
    render(
      <Panel title="Secrets" status={{ tone: "ok", text: "3 stored" }} summary="anthropic, github">
        <p>body</p>
      </Panel>,
    );

    expect(screen.getByRole("heading", { name: "Secrets" })).toBeInTheDocument();
    expect(screen.getByText("3 stored")).toBeInTheDocument();
    expect(screen.getByText("anthropic, github")).toBeInTheDocument();
  });

  it("starts collapsed unless asked to open", () => {
    render(
      <Panel title="Secrets">
        <p>body</p>
      </Panel>,
    );
    expect(details().open).toBe(false);
  });

  it("opens and closes from its summary", () => {
    render(
      <Panel title="Secrets">
        <p>body</p>
      </Panel>,
    );

    fireEvent.click(screen.getByRole("heading", { name: "Secrets" }).closest("summary")!);
    expect(details().open).toBe(true);

    fireEvent.click(screen.getByRole("heading", { name: "Secrets" }).closest("summary")!);
    expect(details().open).toBe(false);
  });

  it("keeps collapsed content mounted, so a form inside keeps its state", () => {
    render(
      <Panel title="Secrets">
        <label htmlFor="x">Name</label>
        <input id="x" defaultValue="typed" />
      </Panel>,
    );
    expect(screen.getByLabelText("Name")).toHaveValue("typed");
  });

  it("reads defaultOpen once, so late data never snaps an open panel shut", () => {
    // Setup opens Repository while nothing is selected; selecting a repo
    // flips defaultOpen to false while someone is still working in it.
    const { rerender } = render(
      <Panel title="Secrets" defaultOpen>
        <p>body</p>
      </Panel>,
    );
    rerender(
      <Panel title="Secrets" defaultOpen={false}>
        <p>body</p>
      </Panel>,
    );
    expect(details().open).toBe(true);
  });
});
