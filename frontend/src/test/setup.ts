import "@testing-library/jest-dom/vitest";

/** jsdom ships no EventSource, so the SSE hook would throw on import.
 *
 * This is a controllable stub rather than a no-op: tests drive it to
 * simulate an open, a drop and a reconnect, which is the behaviour that
 * makes "survives a browser disconnect" true. Instances are collected so a
 * test can reach the one its component created.
 */
export class MockEventSource {
  static instances: MockEventSource[] = [];

  readonly url: string;
  readonly listeners = new Map<string, Set<EventListener>>();
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener): void {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener);
    this.listeners.set(type, set);
  }

  removeEventListener(type: string, listener: EventListener): void {
    this.listeners.get(type)?.delete(listener);
  }

  close(): void {
    this.closed = true;
  }

  /** Dispatches an event of `type` to whatever the component registered. */
  emit(type: string, data: unknown = {}): void {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }

  static reset(): void {
    MockEventSource.instances = [];
  }

  static get last(): MockEventSource | undefined {
    return MockEventSource.instances[MockEventSource.instances.length - 1];
  }
}

Object.defineProperty(globalThis, "EventSource", {
  writable: true,
  configurable: true,
  value: MockEventSource,
});
