import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Notice } from "./Notice";

describe("Notice", () => {
  it("announces a problem as an alert", () => {
    render(<Notice tone="danger">Could not save</Notice>);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not save");
  });

  it("announces a warning as an alert", () => {
    render(<Notice tone="warn">Runs on the host</Notice>);
    expect(screen.getByRole("alert")).toHaveTextContent("Runs on the host");
  });

  it("announces a confirmation politely as status", () => {
    render(<Notice tone="ok">Saved</Notice>);
    expect(screen.getByRole("status")).toHaveTextContent("Saved");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
