import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { App } from "./App";

describe("App", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("submits an order and shows the accepted status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: "accepted", item_id: "sku-1" }),
      }),
    );

    render(<App />);

    fireEvent.change(screen.getByLabelText(/item id/i), { target: { value: "sku-1" } });
    fireEvent.change(screen.getByLabelText(/quantity/i), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: /place order/i }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("sku-1 accepted");
    });
  });

  it("shows an error message when the request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        statusText: "Not Found",
      }),
    );

    render(<App />);

    fireEvent.change(screen.getByLabelText(/item id/i), { target: { value: "unknown" } });
    fireEvent.click(screen.getByRole("button", { name: /place order/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });
});
