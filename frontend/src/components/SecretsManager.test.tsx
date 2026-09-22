import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SecretList } from "../api";
import { SecretsManager } from "./SecretsManager";

function stubSecrets(...responses: SecretList[]) {
  let call = 0;
  const mock = vi.fn(() => {
    const body = responses[Math.min(call, responses.length - 1)];
    call += 1;
    return Promise.resolve({ ok: true, status: 200, json: async () => body });
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

afterEach(() => vi.restoreAllMocks());

const TWO: SecretList = {
  available: true,
  secrets: [
    { scope: "(global)", type: "service", name: "anthropic", state: "(stored)" },
    { scope: "(global)", type: "service", name: "github", state: "(stored)" },
  ],
};

describe("SecretsManager", () => {
  it("lists which services have a secret stored", async () => {
    stubSecrets(TWO);
    render(<SecretsManager />);

    const list = await screen.findByRole("list", { name: /stored secrets/i });
    expect(list).toHaveTextContent("anthropic");
    expect(list).toHaveTextContent("github");
  });

  it("reports what it loaded, so the panel header can show a count", async () => {
    stubSecrets(TWO);
    const seen: SecretList[] = [];
    render(<SecretsManager onSecretsChange={(l) => seen.push(l)} />);

    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]!.secrets.map((s) => s.name)).toEqual(["anthropic", "github"]);
  });

  it("says so when nothing is stored yet", async () => {
    stubSecrets({ available: true, secrets: [] });
    render(<SecretsManager />);
    expect(await screen.findByText(/no secrets stored yet/i)).toBeInTheDocument();
  });

  it("warns when the sbx secret store cannot be read", async () => {
    stubSecrets({ available: false, secrets: [] });
    render(<SecretsManager />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not read the sbx secret store/i);
  });

  it("re-reads the list after the store dialog closes", async () => {
    stubSecrets({ available: true, secrets: [] }, TWO);
    render(<SecretsManager />);
    await screen.findByText(/no secrets stored yet/i);

    fireEvent.click(screen.getByRole("button", { name: /store a secret/i }));
    expect(screen.getByRole("dialog", { name: /store a secret/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(await screen.findByRole("list", { name: /stored secrets/i })).toHaveTextContent(
      "anthropic",
    );
  });
});
