import type { Mock } from "vitest";

/** The `init` a stubbed `fetch` received on call `index`. */
export function fetchInit(mock: Mock, index = 0): RequestInit | undefined {
  return mock.mock.calls[index]?.[1];
}

/** The first `init` a stubbed `fetch` received with this HTTP method. */
export function fetchInitFor(mock: Mock, method: string): RequestInit | undefined {
  const call = mock.mock.calls.find((args) => args[1]?.method === method);

  return call?.[1];
}

/** The JSON a request sent. Assert on it with `toEqual`/`toMatchObject`
 *  rather than casting it to the shape the test hopes it has. */
export function jsonBody(init: RequestInit | undefined) {
  return JSON.parse(String(init?.body));
}

/** Anything `JSON.stringify` round-trips: what a stubbed endpoint can return. */
export type Json = string | number | boolean | null | Json[] | { [key: string]: Json };

/** Stubbed endpoints, keyed by URL prefix. */
export type Routes = Record<string, Json>;
