/** A tab strip for switching between panels in place of scrolling to them.
 *
 * Follows the WAI-ARIA tabs pattern: one tab in the tab order, arrow keys
 * (and Home/End) move between them and select as they go. The caller renders
 * the panels and links them up with `tabId`/`panelId`.
 */

import { useRef, type KeyboardEvent, type ReactNode } from "react";
import { panelId, tabId } from "./tabIds";

export interface TabItem<T extends string> {
  value: T;
  label: string;
  /** Extra content after the label, such as a count. */
  badge?: ReactNode;
}

export function Tabs<T extends string>({
  group,
  label,
  items,
  value,
  onChange,
  className,
}: {
  /** Prefix for element ids, unique on the page. */
  group: string;
  label: string;
  items: TabItem<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}) {
  const refs = useRef(new Map<T, HTMLButtonElement>());

  function handleKey(e: KeyboardEvent<HTMLDivElement>) {
    const index = items.findIndex((item) => item.value === value);
    let next: number | null = null;

    if (e.key === "ArrowRight") next = (index + 1) % items.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + items.length) % items.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = items.length - 1;

    const target = next === null ? undefined : items[next];

    if (target === undefined) return;
    e.preventDefault();
    onChange(target.value);
    refs.current.get(target.value)?.focus();
  }

  return (
    <div role="tablist" aria-label={label} className={className} onKeyDown={handleKey}>
      {items.map((item) => {
        const selected = item.value === value;

        return (
          <button
            key={item.value}
            ref={(el) => {
              if (el) refs.current.set(item.value, el);
              else refs.current.delete(item.value);
            }}
            type="button"
            role="tab"
            id={tabId(group, item.value)}
            aria-controls={panelId(group, item.value)}
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(item.value)}
          >
            {item.label}
            {item.badge}
          </button>
        );
      })}
    </div>
  );
}
