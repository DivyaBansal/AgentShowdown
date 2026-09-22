import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SecretModal } from "./SecretModal";

function stubOk() {
  const mock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ stored: true, service: "github", method: "command" }),
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

afterEach(() => vi.restoreAllMocks());

describe("SecretModal", () => {
  it("is reachable as a dialog by its accessible name", () => {
    stubOk();
    render(<SecretModal onClose={() => {}} />);

    expect(screen.getByRole("dialog", { name: /store a secret/i })).toBeInTheDocument();
  });

  it("moves focus to the first field on open", () => {
    stubOk();
    render(<SecretModal onClose={() => {}} />);

    expect(document.activeElement).toBe(screen.getByLabelText(/service/i));
  });

  it("defaults to the command method, which never stores the value here", () => {
    stubOk();
    render(<SecretModal onClose={() => {}} />);

    expect(screen.getByRole("radio", { name: /command/i })).toBeChecked();
    expect(screen.getByLabelText("Command")).toBeInTheDocument();
  });

  it("warns that a pasted token is less secure", () => {
    stubOk();
    render(<SecretModal onClose={() => {}} />);

    fireEvent.click(screen.getByRole("radio", { name: /paste a token/i }));

    expect(screen.getByRole("alert")).toHaveTextContent(/less secure/i);
  });

  it("posts the chosen method", async () => {
    const mock = stubOk();
    render(<SecretModal onClose={() => {}} />);

    fireEvent.change(screen.getByLabelText(/service/i), { target: { value: "github" } });
    fireEvent.change(screen.getByLabelText("Command"), {
      target: { value: "gh auth token" },
    });
    fireEvent.submit(screen.getByRole("button", { name: /store/i }).closest("form")!);

    await waitFor(() => expect(mock).toHaveBeenCalled());
    const body = JSON.parse(String(mock.mock.calls[0]?.[1]?.body)) as {
      service: string;
      command: string;
    };
    expect(body).toEqual({ service: "github", command: "gh auth token" });
  });

  it("closes on Escape", () => {
    stubOk();
    const onClose = vi.fn();
    render(<SecretModal onClose={onClose} />);

    fireEvent.keyDown(screen.getByRole("dialog").parentElement!, { key: "Escape" });

    expect(onClose).toHaveBeenCalled();
  });

  it("closes when Cancel is pressed", () => {
    stubOk();
    const onClose = vi.fn();
    render(<SecretModal onClose={onClose} />);

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(onClose).toHaveBeenCalled();
  });

  it("surfaces a server error without closing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        statusText: "Bad Gateway",
        json: async () => ({ detail: "sbx is not installed" }),
      }),
    );
    const onClose = vi.fn();
    render(<SecretModal onClose={onClose} />);

    fireEvent.change(screen.getByLabelText("Command"), { target: { value: "x" } });
    fireEvent.submit(screen.getByRole("button", { name: /store/i }).closest("form")!);

    expect(await screen.findByText(/sbx is not installed/i)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
