import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchInit } from "../test/fetch";
import { DeleteJobButton } from "./DeleteJobButton";

afterEach(() => vi.unstubAllGlobals());

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

describe("DeleteJobButton", () => {
  it("asks for confirmation before deleting anything", () => {
    const fetchMock = stubFetch({ ok: true, body: { deleted: "box-1" } });
    render(<DeleteJobButton sandboxName="box-1" />);

    fireEvent.click(screen.getByRole("button", { name: "Delete box-1" }));

    expect(screen.getByText(/branch and pr are kept/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "Delete box-1" })).toBeInTheDocument();
  });

  it("deletes just that job once confirmed", async () => {
    const fetchMock = stubFetch({ ok: true, body: { deleted: "box-1" } });
    render(<DeleteJobButton sandboxName="box-1" />);

    fireEvent.click(screen.getByRole("button", { name: "Delete box-1" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/jobs/box-1");
    expect(fetchInit(fetchMock)?.method).toBe("DELETE");
  });

  it("shows the server's refusal and goes back to the plain button", async () => {
    stubFetch({
      ok: false,
      status: 409,
      body: { detail: "Sandbox 'box-1' still exists -- remove it first" },
    });
    render(<DeleteJobButton sandboxName="box-1" />);

    fireEvent.click(screen.getByRole("button", { name: "Delete box-1" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    expect(await screen.findByText(/remove it first/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete box-1" })).toBeInTheDocument();
  });
});
