import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { LogView } from "./LogView";

afterEach(() => vi.restoreAllMocks());

describe("LogView", () => {
  it("shows the live tail it was given", () => {
    render(<LogView sandboxName="box-1" tail="building...\ndone." />);
    expect(screen.getByText(/building.../)).toBeInTheDocument();
  });

  it("says so when there's no output yet", () => {
    render(<LogView sandboxName="box-1" tail={null} />);
    expect(screen.getByText(/no output yet/i)).toBeInTheDocument();
  });

  it("replaces the tail with a deeper fetch on request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ sandbox_name: "box-1", tail: "the full 2000-line history" }),
      }),
    );
    render(<LogView sandboxName="box-1" tail="just the recent tail" />);

    fireEvent.click(screen.getByRole("button", { name: /load full log/i }));

    await waitFor(() =>
      expect(screen.getByText(/the full 2000-line history/)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /load full log/i })).not.toBeInTheDocument();
  });

  it("alerts when the deeper fetch fails, keeping the live tail visible", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Server Error",
        json: async () => ({ detail: "sandbox unreachable" }),
      }),
    );
    render(<LogView sandboxName="box-1" tail="just the recent tail" />);

    fireEvent.click(screen.getByRole("button", { name: /load full log/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/sandbox unreachable/i),
    );
    expect(screen.getByText(/just the recent tail/)).toBeInTheDocument();
  });

  it("switches to the structured view on request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          sandbox_name: "box-1",
          entries: [{ index: 0, kind: "result", text: "Added the missing import." }],
        }),
      }),
    );
    render(<LogView sandboxName="box-1" tail="raw tail text" />);

    fireEvent.click(screen.getByRole("button", { name: /show structured view/i }));

    await waitFor(() =>
      expect(screen.getByText("Added the missing import.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw tail text")).not.toBeInTheDocument();
  });

  it("alerts when the transcript fetch fails, keeping the raw tail visible", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Server Error",
        json: async () => ({ detail: "sandbox unreachable" }),
      }),
    );
    render(<LogView sandboxName="box-1" tail="raw tail text" />);

    fireEvent.click(screen.getByRole("button", { name: /show structured view/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/sandbox unreachable/i),
    );
    expect(screen.getByText("raw tail text")).toBeInTheDocument();
  });
});
