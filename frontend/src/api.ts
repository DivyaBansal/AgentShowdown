export interface Order {
  item_id: string;
  quantity: number;
}

export interface OrderResponse {
  status: string;
  item_id: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function createOrder(order: Order): Promise<OrderResponse> {
  const res = await fetch("/api/orders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(order),
  });

  if (!res.ok) {
    throw new ApiError(`Failed to create order: ${res.statusText}`, res.status);
  }

  return (await res.json()) as OrderResponse;
}
