import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IssueImporter } from "./IssueImporter";

const ISSUES = [
  { number: 7, title: "Add verbs", body: "- [ ] works", state: "open", url: "u", labels: [] },
  { number: 9, title: "Fix login", body: "", state: "open", url: "u", labels: [] },
];

function stub(issues = ISSUES, importOk = true) {
  const mock = vi.fn((_url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return Promise.resolve({
        ok: importOk,
        status: importOk ? 200 : 503,
        statusText: "Unavailable",
        json: async () =>
          importOk ? { imported: 1 } : { detail: "gh is not installed or not on PATH" },
      });
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({ repo: "owner/app", issues }),
    });
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

afterEach(() => vi.restoreAllMocks());

describe("IssueImporter", () => {
  it("lists issues as checkboxes once loaded", async () => {
    stub();
    render(<IssueImporter repo="owner/app" onImported={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: /load issues/i }));

    expect(await screen.findByRole("checkbox", { name: /#7 add verbs/i })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /#9 fix login/i })).toBeInTheDocument();
  });

  it("imports only the selected issues", async () => {
    const mock = stub();
    render(<IssueImporter repo="owner/app" onImported={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: /load issues/i }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /#7 add verbs/i }));
    fireEvent.click(screen.getByRole("button", { name: /import selected/i }));

    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    const post = mock.mock.calls.find((c) => (c[1] as RequestInit)?.method === "POST");
    const body = JSON.parse(String((post?.[1] as RequestInit).body)) as { issues: number[] };
    expect(body.issues).toEqual([7]);
  });

  it("cannot import with nothing selected", async () => {
    stub();
    render(<IssueImporter repo="owner/app" onImported={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: /load issues/i }));
    await screen.findByRole("checkbox", { name: /#7 add verbs/i });

    expect(screen.getByRole("button", { name: /import selected/i })).toBeDisabled();
  });

  it("says so when there are no issues", async () => {
    stub([]);
    render(<IssueImporter repo="owner/app" onImported={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: /load issues/i }));

    expect(await screen.findByText(/no open issues/i)).toBeInTheDocument();
  });

  it("surfaces an unavailable gh CLI", async () => {
    stub(ISSUES, false);
    render(<IssueImporter repo="owner/app" onImported={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: /load issues/i }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /#7 add verbs/i }));
    fireEvent.click(screen.getByRole("button", { name: /import selected/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/gh is not installed/i);
  });
});
