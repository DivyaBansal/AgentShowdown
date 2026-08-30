import { useState } from "react";
import { ApiError, createOrder } from "./api";

export function App() {
  const [itemId, setItemId] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setStatus(null);
    try {
      const result = await createOrder({ item_id: itemId, quantity });
      setStatus(`Order ${result.item_id} ${result.status}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    }
  }

  return (
    <main>
      <h1>myapp</h1>
      <form onSubmit={handleSubmit}>
        <label htmlFor="item-id">Item ID</label>
        <input
          id="item-id"
          value={itemId}
          onChange={(e) => setItemId(e.target.value)}
          required
        />

        <label htmlFor="quantity">Quantity</label>
        <input
          id="quantity"
          type="number"
          min={1}
          max={1000}
          value={quantity}
          onChange={(e) => setQuantity(Number(e.target.value))}
          required
        />

        <button type="submit">Place order</button>
      </form>

      {status && <p role="status">{status}</p>}
      {error && <p role="alert">{error}</p>}
    </main>
  );
}
