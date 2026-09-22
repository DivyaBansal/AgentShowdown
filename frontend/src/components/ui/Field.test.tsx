import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Field } from "./Field";

describe("Field", () => {
  it("labels the control it wraps", () => {
    render(
      <Field id="branch" label="Base branch">
        <input id="branch" defaultValue="main" />
      </Field>,
    );
    expect(screen.getByLabelText("Base branch")).toHaveValue("main");
  });

  it("shows a hint only when given one", () => {
    const { rerender } = render(
      <Field id="branch" label="Base branch">
        <input id="branch" />
      </Field>,
    );
    expect(screen.queryByText(/merged into/i)).not.toBeInTheDocument();

    rerender(
      <Field id="branch" label="Base branch" hint="Where agent branches are merged into.">
        <input id="branch" />
      </Field>,
    );
    expect(screen.getByText(/merged into/i)).toBeInTheDocument();
  });
});
