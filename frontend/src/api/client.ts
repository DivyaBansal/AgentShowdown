// The one fetch wrapper every backend call goes through: the /api prefix,
// the timeout, and turning an error response into an ApiError.

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Every call here is either quick (a read, or kicking off a job the UI then
// polls for) -- nothing is expected to block on a slow backend operation.
// So one blanket timeout is enough: past this, the backend (or whatever it
// shelled out to) is stuck, not just slow.
const REQUEST_TIMEOUT_MS = 20_000;

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Built conditionally rather than spreading an `undefined` header:
  // exactOptionalPropertyTypes treats an explicit undefined as a real value.
  const options: RequestInit = { ...init };

  if (init?.body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
  }

  const controller = new AbortController();
  options.signal = controller.signal;
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let res: Response;

  try {
    res = await fetch(`/api${path}`, options);
  } catch (err) {
    if (controller.signal.aborted) {
      throw new ApiError(`Request timed out after ${REQUEST_TIMEOUT_MS / 1000}s`, 0);
    }

    throw err;
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    // FastAPI puts the useful part in `detail`; fall back to the status text
    // so a proxy error or a 502 still says something.
    let detail = res.statusText;

    try {
      const body = await res.json();

      if (hasStringDetail(body)) detail = body.detail;
    } catch {
      // Non-JSON error body -- statusText is the best we have.
    }

    throw new ApiError(detail, res.status);
  }

  // SAFETY: T is the response model the caller names for this path, and the
  // interfaces in this directory mirror the backend's Pydantic models. Nothing
  // here re-validates it; a mismatch is a contract bug between the two.
  return (await res.json()) as T;
}

/** FastAPI's `HTTPException` body is `{"detail": "<message>"}`. Validation
 *  errors put a list there instead, which this deliberately rejects. */
function hasStringDetail(body: unknown): body is { detail: string } {
  return (
    typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string"
  );
}
