import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { PreflightBanner } from "./PreflightBanner";

describe("PreflightBanner", () => {
  it("reports readiness when every check passes", () => {
    render(
      <PreflightBanner
        preflight={{
          live_runs_possible: true,
          checks: [{ name: "sbx", ok: true, detail: "/usr/bin/sbx" }],
        }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/environment ready/i);
  });

  it("names the failing check as an alert", () => {
    render(
      <PreflightBanner
        preflight={{
          live_runs_possible: false,
          checks: [
            { name: "sbx", ok: true, detail: "/usr/bin/sbx" },
            { name: "sandboxd", ok: false, detail: "sbx daemon not running" },
          ],
        }}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("sandboxd");
    expect(alert).toHaveTextContent(/daemon not running/i);
    // The passing check is not noise the user needs.
    expect(alert).not.toHaveTextContent("/usr/bin/sbx");
  });

  it("renders nothing before preflight has loaded", () => {
    const { container } = render(<PreflightBanner preflight={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
