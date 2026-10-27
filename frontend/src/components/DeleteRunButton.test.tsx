import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { fetchInit } from "../test/fetch";
import { DeleteRunButton } from "./DeleteRunButton";

afterEach(() => vi.restoreAllMocks());

function stubFetch(response: { ok: boolean; status?: number; body: unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: response.ok,
    status: response.status ?? 200,
    statusText: "",
    json: async () => response.body,
  });

  vi.stubGlobal("fetch", fetchMock);

  return fetchMock;
}

describe("DeleteRunButton", () => {
  it("asks for confirmation before deleting anything", () => {
    const fetchMock = stubFetch({ ok: true, body: { deleted: [] } });
    render(<DeleteRunButton runId="run-0123456789ab" />);

    fireEvent.click(screen.getByRole("button", { name: "Delete run" }));

    expect(screen.getByRole("button", { name: "Confirm delete" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "Delete run" })).toBeInTheDocument();
  });

  it("deletes the run once confirmed", async () => {
    const fetchMock = stubFetch({ ok: true, body: { deleted: ["box-1"] } });
    render(<DeleteRunButton runId="run-0123456789ab" />);

    fireEvent.click(screen.getByRole("button", { name: "Delete run" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/runs/run-0123456789ab");
    expect(fetchInit(fetchMock)?.method).toBe("DELETE");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the server's reason when it refuses", async () => {
    stubFetch({
      ok: false,
      status: 409,
      body: { detail: "Sandbox 'box-1' from run 'run-0123456789ab' still exists" },
    });
    render(<DeleteRunButton runId="run-0123456789ab" />);

    fireEvent.click(screen.getByRole("button", { name: "Delete run" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/still exists/));
    expect(screen.getByRole("button", { name: "Delete run" })).toBeInTheDocument();
  });

  it("can't be started while the run is still active", () => {
    render(<DeleteRunButton runId="run-0123456789ab" disabled />);

    expect(screen.getByRole("button", { name: "Delete run" })).toBeDisabled();
  });
});
