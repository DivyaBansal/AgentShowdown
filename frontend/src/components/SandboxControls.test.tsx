import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { SandboxControls } from "./SandboxControls";

afterEach(() => vi.restoreAllMocks());

const sandboxes = [{ name: "arena-f1-claude", status: "running" }];

describe("SandboxControls", () => {
  it("keeps the wipe disabled until the phrase is typed exactly", () => {
    render(<SandboxControls sandboxes={sandboxes} onChanged={() => {}} />);
    const button = screen.getByRole("button", { name: /remove all sandboxes/i });
    expect(button).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/confirmation/i), {
      target: { value: "kill all" }, // wrong case
    });
    expect(button).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/confirmation/i), {
      target: { value: "KILL ALL" },
    });
    expect(button).toBeEnabled();
  });

  it("warns that the wipe reaches sandboxes this app did not create", () => {
    render(<SandboxControls sandboxes={sandboxes} onChanged={() => {}} />);
    expect(
      screen.getByText(/every sandbox on this machine/i),
    ).toBeInTheDocument();
  });

  it("reports a live agent from a ping", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          sandbox_name: "arena-f1-claude",
          listed: true,
          reachable: true,
          latency_ms: 42,
          agent_alive: true,
          status_file_present: false,
        }),
      }),
    );
    render(<SandboxControls sandboxes={sandboxes} onChanged={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /ping/i }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/agent alive/i),
    );
  });

  it("distinguishes a container that is up from an agent that has died", async () => {
    // The single most useful debugging signal the CLI never surfaced.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          sandbox_name: "arena-f1-claude",
          listed: true,
          reachable: true,
          latency_ms: 7,
          agent_alive: false,
          status_file_present: true,
        }),
      }),
    );
    render(<SandboxControls sandboxes={sandboxes} onChanged={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /ping/i }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/agent not running/i),
    );
  });

  it("says so when nothing is running", () => {
    render(<SandboxControls sandboxes={[]} onChanged={() => {}} />);
    expect(screen.getByText(/no sandboxes are running/i)).toBeInTheDocument();
  });
});

describe("SandboxControls failure paths", () => {
  it("alerts when a ping fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Server Error",
        json: async () => ({ detail: "sbx unreachable" }),
      }),
    );
    render(<SandboxControls sandboxes={sandboxes} onChanged={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /ping/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/sbx unreachable/i),
    );
  });

  it("reports a sandbox that has already gone", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          sandbox_name: "arena-f1-claude",
          listed: false,
          reachable: false,
          latency_ms: null,
          agent_alive: null,
          status_file_present: null,
        }),
      }),
    );
    render(<SandboxControls sandboxes={sandboxes} onChanged={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /ping/i }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/not listed/i),
    );
  });

  it("reports how many jobs the wipe invalidated", async () => {
    const onChanged = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ removed: ["a", "b"], jobs_marked_lost: 2 }),
      }),
    );
    render(<SandboxControls sandboxes={sandboxes} onChanged={onChanged} />);
    fireEvent.change(screen.getByLabelText(/confirmation/i), {
      target: { value: "KILL ALL" },
    });
    fireEvent.click(screen.getByRole("button", { name: /remove all sandboxes/i }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/2 job\(s\) marked lost/i),
    );
    expect(onChanged).toHaveBeenCalled();
  });

  it("alerts when the wipe itself fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Server Error",
        json: async () => ({ detail: "daemon gone" }),
      }),
    );
    render(<SandboxControls sandboxes={sandboxes} onChanged={() => {}} />);
    fireEvent.change(screen.getByLabelText(/confirmation/i), {
      target: { value: "KILL ALL" },
    });
    fireEvent.click(screen.getByRole("button", { name: /remove all sandboxes/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/daemon gone/i),
    );
  });
});
