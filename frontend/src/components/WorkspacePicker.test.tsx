import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkspacePicker } from "./WorkspacePicker";

const INFO = {
  path: "/repos/app",
  exists: true,
  is_git_repo: true,
  origin_url: "https://github.com/owner/app.git",
  github_repo: "owner/app",
  default_branch: "main",
  has_config: true,
  has_features: false,
};

function stub(routes: Record<string, { ok?: boolean; status?: number; body: unknown }>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      // Longest prefix wins: "/api/workspaces/inspect" must not match the
      // "/api/workspaces" stub just because it was declared first.
      const key = Object.keys(routes)
        .filter((r) => url.startsWith(r))
        .sort((a, b) => b.length - a.length)[0];
      const route = key === undefined ? undefined : routes[key];
      return Promise.resolve({
        ok: route?.ok ?? key !== undefined,
        status: route?.status ?? (key === undefined ? 404 : 200),
        statusText: "Not Found",
        json: async () => route?.body ?? {},
      });
    }),
  );
}

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("WorkspacePicker", () => {
  it("offers both ways to point at a repo", async () => {
    stub({ "/api/workspaces": { body: { active: null, workspaces: [] } } });
    render(<WorkspacePicker onChanged={() => {}} />);
    await screen.findByRole("radio", { name: /folder on this machine/i });

    expect(screen.getByRole("radio", { name: /folder on this machine/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /url to clone/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/repository path/i)).toBeInTheDocument();
  });

  it("swaps the input when the GitHub mode is chosen", async () => {
    stub({ "/api/workspaces": { body: { active: null, workspaces: [] } } });
    render(<WorkspacePicker onChanged={() => {}} />);
    await screen.findByRole("radio", { name: /url to clone/i });

    fireEvent.click(screen.getByRole("radio", { name: /url to clone/i }));

    expect(screen.getByLabelText("GitHub URL")).toBeInTheDocument();
  });

  it("reports what inspecting found", async () => {
    stub({
      "/api/workspaces/inspect": { body: INFO },
      "/api/workspaces": { body: { active: null, workspaces: [] } },
    });
    render(<WorkspacePicker onChanged={() => {}} />);

    fireEvent.change(screen.getByLabelText(/repository path/i), "/repos/app");
    fireEvent.submit(screen.getByRole("button", { name: /inspect/i }).closest("form")!);

    expect(await screen.findByText(/existing config found/i)).toBeInTheDocument();
    expect(screen.getByText(/github: owner\/app/i)).toBeInTheDocument();
  });

  it("surfaces a failed inspection as an alert", async () => {
    stub({
      "/api/workspaces/inspect": {
        ok: false,
        status: 404,
        body: { detail: "No such directory: /nope" },
      },
      "/api/workspaces": { body: { active: null, workspaces: [] } },
    });
    render(<WorkspacePicker onChanged={() => {}} />);

    fireEvent.change(screen.getByLabelText(/repository path/i), { target: { value: "/nope" } });
    fireEvent.submit(screen.getByRole("button", { name: /inspect/i }).closest("form")!);

    expect(await screen.findByRole("alert")).toHaveTextContent(/no such directory/i);
  });

  it("selects a repo and tells its parent", async () => {
    const onChanged = vi.fn();
    stub({
      "/api/workspaces/inspect": { body: INFO },
      "/api/workspaces/select": { body: { path: "/repos/app" } },
      "/api/workspaces": { body: { active: null, workspaces: [] } },
    });
    render(<WorkspacePicker onChanged={onChanged} />);

    fireEvent.change(screen.getByLabelText(/repository path/i), { target: { value: "/repos/app" } });
    fireEvent.submit(screen.getByRole("button", { name: /inspect/i }).closest("form")!);
    fireEvent.click(await screen.findByRole("button", { name: /use this repository/i }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(screen.getByRole("status")).toHaveTextContent("/repos/app");
  });

  it("shows a progress indicator while cloning", async () => {
    stub({
      "/api/workspaces/clone": {
        body: { clone_id: "c1", target_path: "/home/x/clones/owner-app", state: "running" },
      },
      "/api/workspaces": { body: { active: null, workspaces: [] } },
    });
    render(<WorkspacePicker onChanged={() => {}} />);

    fireEvent.click(screen.getByRole("radio", { name: /url to clone/i }));
    fireEvent.change(screen.getByLabelText("GitHub URL"), {
      target: { value: "https://github.com/owner/app" },
    });
    fireEvent.submit(screen.getByRole("button", { name: /clone/i }).closest("form")!);

    expect(await screen.findByRole("progressbar")).toBeInTheDocument();
  });

  it("lists known repositories with a way to switch", async () => {
    stub({
      "/api/workspaces": {
        body: {
          active: "/repos/one",
          workspaces: [
            { path: "/repos/one", origin_url: null, github_repo: null, cloned: false },
            { path: "/repos/two", origin_url: null, github_repo: null, cloned: false },
          ],
        },
      },
    });
    render(<WorkspacePicker onChanged={() => {}} />);

    expect(await screen.findByText("/repos/two")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /switch/i })).toHaveLength(1);
  });
});

describe("WorkspacePicker clone progress", () => {
  /** A clone runs in the background, so the picker has to collect the result
   *  separately. Without that the progress bar never goes away. */
  function stubClone(finalState: "finished" | "failed", detail = "") {
    let polls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        let body: unknown = { active: null, workspaces: [] };
        if (url.includes("/clone/")) {
          polls += 1;
          body =
            polls === 1
              ? { clone_id: "c1", state: "running", target_path: "/c/owner-app", detail: "" }
              : {
                  clone_id: "c1",
                  state: finalState,
                  target_path: "/c/owner-app",
                  detail,
                  github_repo: "owner/app",
                };
        } else if (url.endsWith("/workspaces/clone")) {
          body = { clone_id: "c1", state: "running", target_path: "/c/owner-app" };
        }
        return Promise.resolve({ ok: true, status: 200, statusText: "OK", json: async () => body });
      }),
    );
  }

  async function startClone() {
    render(<WorkspacePicker onChanged={vi.fn()} />);
    await screen.findByRole("radio", { name: /url to clone/i });
    fireEvent.click(screen.getByRole("radio", { name: /url to clone/i }));
    fireEvent.change(screen.getByLabelText("GitHub URL"), {
      target: { value: "https://github.com/owner/app" },
    });
    fireEvent.submit(screen.getByRole("button", { name: /clone/i }).closest("form")!);
    await screen.findByRole("progressbar");
  }

  it("clears the progress bar once the clone finishes", async () => {
    stubClone("finished");
    await startClone();

    await vi.advanceTimersByTimeAsync(2500);

    await waitFor(() => expect(screen.queryByRole("progressbar")).not.toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent(/cloned into/i);
  });

  it("reports a failed clone instead of spinning forever", async () => {
    stubClone("failed", "Repository not found");
    await startClone();

    await vi.advanceTimersByTimeAsync(2500);

    await waitFor(() => expect(screen.queryByRole("progressbar")).not.toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/repository not found/i);
  });
});
